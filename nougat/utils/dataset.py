"""
Donut
Copyright (c) 2022-present NAVER Corp.
MIT License
Copyright (c) Meta Platforms, Inc. and affiliates.
"""
import logging
import os
from math import prod
from pathlib import Path
from functools import partial
import random
from typing import Dict, Tuple, Callable
from PIL import Image, UnidentifiedImageError
from typing import List, Optional

import torch
import pypdf
import orjson
from torch.utils.data import Dataset
from transformers.modeling_utils import PreTrainedModel
from nougat.dataset.rasterize import rasterize_paper


class ImageDataset(torch.utils.data.Dataset):
    """
    Dataset for processing a list of images using a preparation function.

    This dataset takes a list of image paths and applies a preparation function to each image.

    Args:
        img_list (list): List of image paths.
        prepare (Callable): A preparation function to process the images.

    Attributes:
        img_list (list): List of image paths.
        prepare (Callable): The preparation function.
    """

    def __init__(self, img_list, prepare: Callable):
        super().__init__()
        self.img_list = img_list
        self.prepare = prepare

    def __len__(self):
        return len(self.img_list)

    @staticmethod
    def ignore_none_collate(batch):
        if batch is None:
            return
        try:
            batch = [x for x in batch if x is not None and x[0] is not None]
            if len(batch) == 0:
                return
            return torch.utils.data.dataloader.default_collate(batch)
        except AttributeError:
            pass

    def __getitem__(self, idx):
        try:
            img = Image.open(self.img_list[idx])
            return self.prepare(img)
        except Exception as e:
            logging.error(e)


class LazyDataset(Dataset):
    """
    Lazy loading dataset for processing PDF documents.

    This dataset allows lazy loading of PDF documents and provides access to processed images
    using a specified preparation function.

    Args:
        pdf (str): Path to the PDF document.
        prepare (Callable): A preparation function to process the images.

    Attributes:
        name (str): Name of the PDF document.
    """

    def __init__(self, pdf, prepare: Callable, pages: Optional[List[int]] = None):
        super().__init__()
        self.prepare = prepare
        self.name = str(pdf)
        self.init_fn = partial(rasterize_paper, pdf, pages=pages)
        self.dataset = None
        self.size = len(pypdf.PdfReader(pdf).pages) if pages is None else len(pages)

    def __len__(self):
        return self.size

    def __getitem__(self, i):
        if i == 0 or self.dataset is None:
            self.dataset = ImageDataset(self.init_fn(), self.prepare)
        if i <= self.size and i >= 0:
            return self.dataset[i], self.name if i == self.size - 1 else ""
        else:
            raise IndexError

    @staticmethod
    def ignore_none_collate(batch):
        if batch is None:
            return None, None
        try:
            _batch = []
            for i, x in enumerate(batch):
                image, name = x
                if image is not None:
                    _batch.append(x)
                elif name:
                    if i > 0:
                        _batch[-1] = (_batch[-1][0], name)
                    elif len(batch) > 1:
                        _batch.append((batch[1][0] * 0, name))
            if len(_batch) == 0:
                return None, None
            return torch.utils.data.dataloader.default_collate(_batch)
        except AttributeError:
            pass
        return None, None


class SciPDFDataset(Dataset):
    """
    Custom dataset for scientific PDF data.

    This dataset loads data from JSONL files and provides access to images, ground truth,
    and metadata.

    Args:
        path_to_index (str): Path to the index file.
        split (str, optional): Split of the dataset (e.g., "train", "test"). Default is "train".
        root_name (str, optional): Root directory name. Default is an empty string.
        template (str, optional): Template for split naming. Default is "%s".

    Attributes:
        empty_sample: Placeholder for empty samples.
    """

    empty_sample = None

    def __init__(
        self,
        path_to_index: str,
        split: str = "train",
        root_name="",
        template="%s",
    ) -> None:
        super().__init__()
        self.path_to_index = Path(path_to_index)
        self.root_name = root_name
        self.path_to_root = self.path_to_index.parent
        if not split in self.path_to_index.stem:
            pti = self.path_to_root / (template % split + ".jsonl")
            if pti.exists():
                self.path_to_index = pti
            else:
                raise ValueError(f'Dataset file for split "{split}" not found: {pti}')
        self.dataset_file = None  # mulitprocessing
        # load seek map
        seek_path = self.path_to_root / (self.path_to_index.stem + ".seek.map")
        if seek_path.exists():
            self.seek_map = orjson.loads(seek_path.open().read())
        else:
            raise ValueError(
                'No "%s" found in %s' % (seek_path.name, str(self.path_to_root))
            )
        self.dataset_length = len(self.seek_map)

    def __len__(self) -> int:
        return self.dataset_length

    def __getitem__(self, index: int) -> Dict:
        position = self.seek_map[index]
        if self.dataset_file is None:
            self.dataset_file = self.path_to_index.open()
        self.dataset_file.seek(position)
        line = self.dataset_file.readline()
        try:
            data: Dict = orjson.loads(line)
        except Exception as e:
            logging.info(
                "JSONL for sample %i could not be loaded at position %i: %s\n%s",
                index,
                position,
                str(e),
                line,
            )
            return self.empty_sample
        img_path: Path = self.path_to_root / self.root_name / data.pop("image")
        if not img_path.exists():
            logging.info("Sample %s could not be found.", img_path)
            return self.empty_sample
        try:
            img = Image.open(img_path)
        except UnidentifiedImageError:
            logging.info("Image %s could not be opened.", img_path)
            return self.empty_sample
        return {"image": img, "ground_truth": data.pop("markdown"), "meta": data}

    def __iter__(self):
        for i in range(self.dataset_length):
            yield self[i]

# custom dataset for this research
class CustomDataset(Dataset):
    """
    Args:
        train_path: the path to the train dataset
        valid_path: the path to the validation dataset
        test_path: the path to the test dataset
    """

    

    def __init__(
        self,
        image_list_path: str,
        sentences_path: str,
        #valid_jsonl_path: str,
        image_path: str,
        split: str = "train"
    ):
        super().__init__()
        #self.train_jsonl_path: str = train_jsonl_path
        #self.valid_jsonl_path: str = valid_jsonl_path
        self.image_list_path: str = image_list_path
        self.sentences_path: str = sentences_path
        self.image_path: str = image_path
        self.split: str = split
        self.img_list: List[str] = None

        try:
            with open(image_list_path) as w:
                self.img_list = w.readlines()
        except Exception:
            raise Exception("load train_jsonl failed.")
        """
        try:
            with open(valid_jsonl_path) as w:
                if self.valid_meta == None:
                    self.valid_meta = w.readlines()
        except Exception:
            raise Exception("load valid_jsonl failed.")
        """
    
    def __getitem__(self, idx: int) -> Dict:
        """
        inside NougatDataset the program expects that calling self.dataset[idx] returns a dict that contains
        key "image", key "ground_truth" and "meta"
        """
        # load array of images
        meta_list: List[str] = self.img_list if self.split == "train" else [""]
        pic_path_short: str = meta_list[idx].replace("\n", "")
        pic_path: str = os.path.join(self.image_path, pic_path_short)

        try:
            img: Image.Image = Image.open(pic_path)
        except:
            print(f"cannot load for {pic_path}")
            return None
        txt_path_short: str = pic_path_short.replace("/","_")+".txt"
        txt_path = os.path.join(self.sentences_path, txt_path_short)

        try:
            with open(txt_path, 'r') as w:
                sentence = w.read()
        except:
            print(f"cannot retrieve sentence in {txt_path}")
            return None

        return {"image": img, "ground_truth": sentence, "meta": {"img_path": pic_path}}
    
        """
        # left case
        if pic_path.endswith(".left.png"):
            img = Image.open(os.path.join(self.image_path, pic_path.replace(".left.png", ".tif")))
            img = img.crop((0, 0, img.width//2, img.height)) # left crop
        # right case
        elif pic_path.endswith(".right.png"):
            img = Image.open(os.path.join(self.image_path, pic_path.replace(".right.png", ".tif")))
            img = img.crop((img.width//2, 0, img.width, img.height)) # right crop
        # other case (should not happen but just in case)
        else:
            try:
                img = Image.open(os.path.join(self.image_path, pic_path.replace(".png", ".tif")))
            except:
                raise Exception("No such image exist.")
        """

        #return {"image": img, "ground_truth": metadata["sentence"], "meta": metadata}

    def __len__(self) -> int:
        return len(self.img_list) if self.split == "train" else 0 #len(self.valid_meta)

    def __iter__(self):
        for i in range(len(self)):
            yield self[i]
    
class NougatDataset(Dataset):
    """
    Args:
        dataset_path: the path to the jsonl file
    """

    def __init__(
        self,
        image_list_path,
        sentences_path,
        image_path,
        nougat_model: PreTrainedModel,
        max_length: int,
        split: str = "train",
        root_name: str = "arxiv",
    ):
        super().__init__()
        self.nougat_model = nougat_model
        self.max_length = max_length
        self.split = split
        self.perturb = "NOUGAT_PERTURB" in os.environ and os.environ["NOUGAT_PERTURB"]
        # TODO improve naming conventions
        template = "%s"
        self.dataset = CustomDataset(
            image_list_path, sentences_path, image_path, self.split
        )
        self.dataset_length = len(self.dataset)

    def __len__(self) -> int:
        return self.dataset_length

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Load image from image_path of given dataset_path and convert into input_tensor and labels.
        Convert gt data into input_ids (tokenized string)

        Returns:
            input_tensor : preprocessed image
            input_ids : tokenized gt_data
        """
        sample = self.dataset[idx]
        if sample is None:
            # if sample is broken choose another randomly
            return self[random.randint(0, self.dataset_length - 1)]
        if sample is None or sample["image"] is None or prod(sample["image"].size) == 0:
            input_tensor = None
        else:
            input_tensor = self.nougat_model.encoder.prepare_input(
                sample["image"], random_padding=self.split == "train"
            )

        tokenizer_out = self.nougat_model.decoder.tokenizer(
            sample["ground_truth"],
            max_length=self.max_length,
            padding="max_length",
            return_token_type_ids=False,
            truncation=True,
            return_tensors="pt",
        )
        input_ids = tokenizer_out["input_ids"].squeeze(0)
        attention_mask = tokenizer_out["attention_mask"].squeeze(0)
        # randomly perturb ground truth tokens
        if self.split == "train" and self.perturb:
            # check if we perturb tokens
            unpadded_length = attention_mask.sum()
            while random.random() < 0.1:
                try:
                    pos = random.randint(1, unpadded_length - 2)
                    token = random.randint(
                        23, len(self.nougat_model.decoder.tokenizer) - 1
                    )
                    input_ids[pos] = token
                except ValueError:
                    break
        return input_tensor, input_ids, attention_mask
