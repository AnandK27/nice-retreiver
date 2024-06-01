from PIL import Image
from transformers import Blip2Processor, Blip2ForConditionalGeneration
import torch

from torch.utils.data import Dataset, DataLoader
import torch.nn as nn
import numpy as np

import pickle
import os
import sys


class TrainDataset(Dataset):
    def __init__(self, k = 1, path='/3d_data/datasets/coco/', knn_file = 'knn/kNN.npy', dict_file ='image_name.pickle', image_2_cap = 'image_name_2_captions.pickle'):

        self.root = path
        self.kNN = np.load(self.root + knn_file, allow_pickle=True)

        with open(self.root + dict_file, 'rb') as handle:
            self.max_caption_dict = pickle.load(handle)

        with open(self.root + image_2_cap, 'rb') as handle:
            self.image_caption_dict = pickle.load(handle)

        self.img_names = sorted(list(self.max_caption_dict.keys()))

        self.device = "cuda" if torch.cuda.is_available() else "cpu"

        self.img_names = [name.split('/')[1].split('.')[0] for name in self.img_names]

        self.processor = Blip2Processor.from_pretrained("Salesforce/blip2-opt-2.7b")

    def __getitem__(self, idx):
        #img embedding, caption embedding, kNN scores, kNN indices

        img_name = self.img_names[idx]
        image = Image.open(self.root + 'train2014/' + img_name + '.jpg')

        scores, indices = self.kNN[idx]
        
        max_caption = self.image_caption_dict[self.img_names[int(indices[0])] + '.jpg'][self.max_caption_dict['train_emb/'+self.img_names[int(indices[0])]+'.npy']]

        inputs = self.processor(images=image, text=max_caption + ' Rephrase',return_tensors="pt",  padding='max_length', truncation=True, max_length = 20).to(self.device, torch.float16)

        caption = self.image_caption_dict[img_name + '.jpg'][self.max_caption_dict['train_emb/'+ img_name + '.npy']]

        caption_ids = self.processor.tokenizer(text = caption, return_tensors="pt", padding='max_length', truncation=True, max_length = 20).input_ids.to(self.device)

        return inputs.input_ids.to(self.device).squeeze(0), inputs.pixel_values.to(self.device).squeeze(0), inputs.attention_mask.to(self.device).squeeze(0), caption_ids.to(self.device).squeeze(0)

    def __len__(self):
        return len(self.img_names)
    

if __name__ == '__main__':
    batch_size = int(sys.argv[1])

    train_data = TrainDataset()
    train_loader = DataLoader(train_data, batch_size=batch_size, shuffle=True, num_workers=8, pin_memory=True)

    model = Blip2ForConditionalGeneration.from_pretrained(
    "Salesforce/blip2-opt-2.7b", load_in_8bit=True, device_map={"": 0}, torch_dtype=torch.float16)

    for param in model.language_model.parameters():
        param.requires_grad = False

    for param in model.vision_model.parameters():
        if type(param) == torch.nn.parameter.Parameter:
            param.requires_grad = False 

    criterion = nn.CrossEntropyLoss()
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.1)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=100, eta_min=0.0001)

    save_path = '/3d_data/retreiver/base/'
    if not os.path.exists(save_path):
        os.makedirs(save_path)

    model.train()

    best_loss = 1000

    print('==================== Training Started ====================')
    for epoch in range(100):
        for i, (input_ids, pixel_values, attenion_masks, caption_ids) in enumerate(train_loader):
            optimizer.zero_grad()

            outputs = model(pixel_values = pixel_values, input_ids = input_ids, attention_mask = attenion_masks, labels=caption_ids)
            loss = outputs.loss
            
            loss.backward()
            optimizer.step()
            scheduler.step()

            if loss.item() < best_loss:
                best_loss = loss.item()
                torch.save(model.state_dict(), save_path + 'best_model.pt')
                print(f'Best Model Saved with Loss: {best_loss:.4f}')

        print(f'Epoch: {epoch+1}, Loss: {loss.item():.4f}')


    for i, data in enumerate(train_loader):
        print(data)
        break