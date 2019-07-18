from recovery import checkmana
from sss import *
import threading
import datetime


def skill(key, ct):
    print('施放%s技能...' % key)
    ks = int(key_dict[key], 16)
    PressKey(ks)
    time.sleep(ct)
    ReleaseKey(ks)


dist_x = findcharx()
print('現在時間:', datetime.datetime.now())
start = time.time()
print('Ready.', end='')
time.sleep(1)
print('.', end='')
time.sleep(1)
print('.')
time.sleep(1)

# 多線程
threadObj = threading.Thread(name='MP', target=checkmana)
threadObj.start()
# 找角色位置

print('人物所在位置於', dist_x)

while True:
    skill("DIK_X", 4)
    checkpos(dist_x)
    # skill("DIK_X", 3)
    # checkpos(dist_x+27)
    # skill("DIK_X", 3)
    # checkpos(dist_x+55)
    if time.time()-start>=120:
        skill("DIK_8", 4.2)
        start = time.time()
