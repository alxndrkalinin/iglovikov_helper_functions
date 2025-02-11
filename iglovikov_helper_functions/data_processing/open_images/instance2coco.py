"""
The script reads the data from openimages Challenge 2019

https://www.kaggle.com/c/open-images-2019-instance-segmentation

CSV files + images + masks and outputs json file with instance
segmentation labels in COCO format.


Requires:

<X> = train or validation


challenge-2019-<X>-segmentation-masks.csv
challenge-2019-classes-description-segmentable.csv

path to masks
path to images
"""

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import pandas as pd
from joblib import Parallel, delayed
from PIL import Image
from tqdm import tqdm

from iglovikov_helper_functions.utils.mask_tools import binary_mask2coco, coco_seg2bbox


def get_annotation_info(image_id: str, dft: pd.DataFrame, hash2id: dict, image_sizes: dict, mask_path: Path) -> list:
    image_width, image_height = image_sizes[image_id]

    for i in dft.index:
        mask_file_name = dft.loc[i, "MaskPath"]

        png = (cv2.imread(str(mask_path / mask_file_name), 0) > 0).astype(np.uint8)
        png = cv2.resize(png, (image_width, image_height), cv2.INTER_NEAREST)

        segmentation = binary_mask2coco(png)
        bbox = coco_seg2bbox(segmentation, image_height, image_width)
        annotation_id = str(hash(image_id + "_{}".format(i)))

        class_name = dft.loc[i, "LabelName"]

        area = bbox[2] * bbox[3]  # bbox_width * bbox_height

        annotation_info = {
            "id": annotation_id,
            "image_id": image_id,
            "category_id": hash2id[class_name],
            "iscrowd": 0,
            "area": area,
            "bbox": bbox,
            "segmentation": segmentation,
        }

    return annotation_info


def get_coco_images(annotations, image_sizes):
    coco_images = []

    for image_id in tqdm(annotations["ImageID"].unique()):
        image_width, image_height = image_sizes[image_id]

        image_name = image_id + ".jpg"

        image_info = {"id": image_id, "file_name": image_name, "width": image_width, "height": image_height}

        coco_images.append(image_info)

    return coco_images


def get_name2size(image_path: Path, num_jobs: int, extenstion: str = "jpg", id_type: str = "stem") -> dict:
    """Return image to size mapping.

    Args:
        image_path: Path where images are stored.
        num_jobs: number of CPU threads to use.
        extenstion: 'jpg' or 'png'
        id_type: `name` or `stem`

    Returns: {<file_name>}: (width, height)

    """

    def helper(x):
        image = Image.open(x)
        if id_type == "stem":
            return x.stem, image.size
        elif id_type == "name":
            return x.name, image.size
        else:
            raise NotImplementedError("only name and stem are supported")

    sizes = Parallel(n_jobs=num_jobs, prefer="threads")(
        delayed(helper)(file_name) for file_name in tqdm(sorted(image_path.glob("*.{}".format(extenstion))))
    )

    return dict(sizes)


def get_classhash2id(categories_path: Path) -> dict:
    """Create mapping from class hash to category_id. Categories start with 1.

    Args:
        categories_path: Path to the file challenge-2019-classes-description-segmentable.csv

    Returns: {class_hash: category_id}

    """
    classes = pd.read_csv(str(categories_path), header=None)

    return dict(zip(classes[0].values, classes.index + 1))


def get_coco_categories(categories_path: Path) -> list:
    """Create coco categories dice

    Args:
        categories_path: Path to the file challenge-2019-classes-description-segmentable.csv

    Returns: [{'id': category_id, 'name': class_name, 'supercategory': class_name]

    """

    classes = pd.read_csv(str(categories_path), header=None)

    coco_categories = []

    for i in classes.index:
        class_name = classes.loc[i, 1]

        coco_categories.append({"id": i + 1, "name": class_name, "supercategory": class_name})

    return coco_categories


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("-a", "--annotation", type=str, help="Path to the annotation file.")
    parser.add_argument("-i", "--image_path", type=Path, help="Path to images.")
    parser.add_argument("-m", "--mask_path", type=Path, help="Path to masks.")
    parser.add_argument("-c", "--classes", type=Path, help="Path to file with class mapping.")

    parser.add_argument("-o", "--output", type=str, help="Path where to store pickled data.", required=True)
    parser.add_argument("-j", "--num_jobs", type=int, default=1, help="Number of jobs to spawn.")
    return parser.parse_args()


def main():
    args = parse_args()
    annotation = pd.read_csv(args.annotation)

    image_sizes = get_name2size(args.image_path, args.num_jobs, "jpg", id_type="stem")
    mask_sizes = get_name2size(args.mask_path, args.num_jobs, "png", id_type="name")

    hash2id = get_classhash2id(args.classes)

    annotation["size"] = annotation["ImageID"].map(image_sizes)

    print(f"Masks before purge = {annotation.shape[0]}")

    annotation = annotation[annotation["size"].notnull()]
    annotation["mask_sizes"] = annotation["MaskPath"].map(mask_sizes)

    valid_index = annotation["size"].notnull() & annotation["mask_sizes"].notnull()

    annotation = annotation[valid_index]

    print(f"Masks after purge = {annotation.shape[0]}")

    grouped_annotations = annotation.groupby("ImageID")

    coco_annotations = Parallel(n_jobs=args.num_jobs, prefer="threads")(
        delayed(get_annotation_info)(image_id, group, hash2id, image_sizes, args.mask_path)
        for image_id, group in tqdm(grouped_annotations)
    )

    samples = {
        "categories": get_coco_categories(args.classes),
        "images": get_coco_images(annotation, image_sizes),
        "annotations": coco_annotations,
    }

    with open(args.output, "wb") as f:
        json.dump(samples, f)


if __name__ == "__main__":
    main()
