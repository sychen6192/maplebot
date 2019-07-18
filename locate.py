from PIL import Image, ImageGrab
import pyautogui
import cv2
import numpy as np


def findcharx():
    image = ImageGrab.grab()
    for x in range(1, 400):
        for y in range(1, 400):
            px = image.getpixel((x, y))
            if px[0] == 255 and px[1] == 255 and px[2] == 0:
                return x
# print(findcharx())