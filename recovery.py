from keyboard import *
import pyautogui
import time


def checkmana():
    while True:
        while not pyautogui.locateOnScreen('data/mana.png'):
            print('魔力低於30%...')
            PressKey(0x1F)
            time.sleep(0.25)
            ReleaseKey(0x1F)
            time.sleep(0.5)


# while pyautogui.locateOnScreen('data/door.png'):
#     print('Open mystic door')
#     PressKey(0xC7)
