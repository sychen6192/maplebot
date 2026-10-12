"""改 YAML 設定檔：能就地改值就保留註解，做不到才整份重寫。

- merge_into：讀出、深層合併、safe_dump 寫回。值一定對，註解全部會掉。
- patch_scalars：只改 patch 裡那幾個值，其餘一個字都不動；做不到回 None。
- write_patch：先試 patch_scalars，不行才退回 merge_into；註解要掉之前留一份 .bak。

原本放在 gui/settings.py，但 tools/calibrate.py 也要寫 local.yaml，為了這個
import GUI 套件，工具就反過來依賴控制台了。這裡只靠 PyYAML：不碰 tkinter，
也不 import config——config.py 反過來從這裡拿 _deep_merge，讀設定時
local.yaml 怎麼蓋過 default.yaml，跟存檔時怎麼合併，才會是同一套規則。
"""
import os
import re
import shutil
from typing import Any, List, Optional, Sequence, Tuple

import yaml


def merge_into(path: str, patch: dict) -> dict:
    """讀出既有 YAML、深層合併 patch 後寫回。

    local.yaml 裡還有 regions（校正結果）等 UI 沒有的欄位，直接覆寫會把它們
    洗掉——使用者按一次「保存」就要重新校正，這是絕對不能發生的事。
    """
    data = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    merged = _deep_merge(data, patch)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(merged, f, allow_unicode=True, sort_keys=False)
    return merged


def _deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# 改數值時保留 YAML 註解
# ---------------------------------------------------------------------------

_MISSING = object()


def _leaves(patch: dict, prefix=()):
    for k, v in patch.items():
        if isinstance(v, dict):
            yield from _leaves(v, prefix + (k,))
        else:
            yield prefix + (k,), v


def _lookup(data: Any, path: Sequence[str]) -> Any:
    for k in path:
        if not isinstance(data, dict) or k not in data:
            return _MISSING
        data = data[k]
    return data


def _scalar_text(value: Any) -> Optional[str]:
    """單一純量的 YAML 寫法（'' 會加引號、'8' 保持字串）；清單或 dict 回 None。"""
    if isinstance(value, (dict, list, tuple)):
        return None
    s = yaml.safe_dump(value, allow_unicode=True, width=1 << 16)
    if s.endswith("\n...\n"):
        s = s[:-len("\n...\n")]
    s = s.rstrip("\n")
    return None if "\n" in s else s


def _content(line: str) -> str:
    return line.rstrip("\r\n")


def _indent_of(content: str) -> int:
    return len(content) - len(content.lstrip(" "))


def _is_skip(content: str) -> bool:
    s = content.strip()
    return not s or s.startswith("#")


def _key_rest(content: str, key: str) -> Optional[str]:
    """這行是 `key:` 的話回傳冒號後面的部分，否則 None。"""
    s = content.lstrip(" ")
    head = key + ":"
    if not s.startswith(head):
        return None
    rest = s[len(head):]
    return rest if (not rest or rest[0] in " \t") else None


def _find_key_line(lines: List[str], path: Sequence[str]) -> Optional[int]:
    """照 path 一層層找 block 風格的 `key:` 行；flow 風格、找不到、重複都回 None。"""
    start, end = 0, len(lines)
    for depth, key in enumerate(path):
        child = None
        hit = None
        for i in range(start, end):
            c = _content(lines[i])
            if _is_skip(c):
                continue
            ind = _indent_of(c)
            if child is None:
                child = ind
            if ind != child or _key_rest(c, key) is None:
                continue
            if hit is not None:
                return None                         # 重複的 key：不猜
            hit = i
        if hit is None:
            return None
        if depth == len(path) - 1:
            return hit
        rest = _key_rest(_content(lines[hit]), key).strip()
        if rest and not rest.startswith("#"):
            return None                             # `potions: {hp: ...}` 這種 flow 風格
        start, end = hit + 1, hit + 1
        while end < len(lines):
            c = _content(lines[end])
            if not _is_skip(c) and _indent_of(c) <= child:
                break
            end += 1
    return None


def _split_value(body: str) -> Optional[Tuple[str, str]]:
    """'0.6   # 註解' -> ('0.6', '   # 註解')；引號沒收尾回 None。"""
    if body[0] in "'\"":
        q, i = body[0], 1
        while i < len(body):
            if q == '"' and body[i] == "\\":
                i += 2
                continue
            if body[i] == q:
                if q == "'" and body[i + 1:i + 2] == "'":
                    i += 2
                    continue
                break
            i += 1
        else:
            return None
        value = body[:i + 1]
    else:
        m = re.search(r"[ \t]#", body)
        value = body[:m.start() if m else len(body)].rstrip()
    return value, body[len(value):]


def _replace_value(line: str, key: str, new: str) -> Optional[str]:
    c = _content(line)
    eol = line[len(c):]
    indent = c[:_indent_of(c)]
    rest = _key_rest(c, key)
    body = rest.lstrip(" \t")
    gap = rest[:len(rest) - len(body)]
    if not body or body[0] in "#[{|>&*!":
        return None                                 # 值在下一行、flow 集合、區塊字串、錨點
    split = _split_value(body)
    if split is None:
        return None
    old, tail = split
    if tail:
        # 盡量讓行尾註解留在原本那一欄
        comment = tail.lstrip(" \t")
        pad = max(1, len(tail) - len(comment) + len(old) - len(new))
        tail = " " * pad + comment
    return f"{indent}{key}:{gap}{new}{tail}{eol}"


def _block_end(lines: List[str], idx: int) -> int:
    """`key:` 那行底下屬於它的區塊到哪一行為止（不含）。

    YAML 的清單可以跟 key 同一層縮排（`  waypoints:` 底下接 `  - x: 24`），
    所以同縮排但以 '- ' 開頭的也算。最後一個項目後面、縮排更深的註解是那個
    項目的說明，一起帶走；空行和縮排更淺的註解留給下一個 key。
    """
    key_ind = _indent_of(_content(lines[idx]))
    last = idx
    j = idx + 1
    while j < len(lines):
        c = _content(lines[j])
        if _is_skip(c):
            j += 1
            continue
        s = c.lstrip(" ")
        if _indent_of(c) > key_ind or (_indent_of(c) == key_ind
                                        and (s == "-" or s.startswith("- "))):
            last = j
            j += 1
            continue
        break
    j = last + 1
    while j < len(lines):
        c = _content(lines[j])
        if not c.strip() or not c.strip().startswith("#") or _indent_of(c) <= key_ind:
            break
        last = j
        j += 1
    return last + 1


def _item_text(item: Any) -> Optional[str]:
    if isinstance(item, (dict, list)):
        s = yaml.safe_dump(item, default_flow_style=True, allow_unicode=True,
                           sort_keys=False, width=1 << 16).strip()
        return None if "\n" in s else s
    return _scalar_text(item)


def _same_line_value(body: str) -> Optional[Tuple[str, str]]:
    """key 同一行的值與行尾註解：'auto  # x' 或 '[30, 90]  # x'。"""
    if body.startswith("["):
        end = body.find("]")
        if end < 0:
            return None
        return body[:end + 1], body[end + 1:]
    if body[0] in "{|>&*!":
        return None
    return _split_value(body)


def _replace_node(lines: List[str], idx: int, key: str, value: Any) -> Optional[List[str]]:
    """把 `key:` 的值換成 value，回傳新的行；做不到回 None。

    純量換純量只改那一行。值是清單（巡邏點、buff）時只換掉那個區塊——
    以前清單一律整份重寫，錄一次路線就把檔案其他地方的實測註解全部洗掉。
    """
    c = _content(lines[idx])
    eol = lines[idx][len(c):] or "\n"
    indent = c[:_indent_of(c)]
    rest = _key_rest(c, key)
    body = rest.lstrip(" \t")
    if not isinstance(value, (list, tuple)):
        if body and not body.startswith("#"):
            line = _replace_value(lines[idx], key, _scalar_text(value) or "")
            return None if line is None or _scalar_text(value) is None \
                else lines[:idx] + [line] + lines[idx + 1:]
        new = _scalar_text(value)                   # 區塊（清單）換回純量，例如 auto
        if new is None:
            return None
        tail = (" " + body) if body else ""
        end = _block_end(lines, idx)
        return lines[:idx] + [f"{indent}{key}: {new}{tail}{eol}"] + lines[end:]
    items = [_item_text(v) for v in value]
    if any(t is None for t in items):
        return None
    if body and not body.startswith("#"):
        split = _same_line_value(body)
        if split is None:
            return None
        comment = split[1].strip()
        end = idx + 1
    else:
        comment = body.strip()
        end = _block_end(lines, idx)
    tail = ("  " + comment) if comment else ""
    if not items:
        return lines[:idx] + [f"{indent}{key}: []{tail}{eol}"] + lines[end:]
    block = [f"{indent}  - {t}{eol}" for t in items]
    return lines[:idx] + [f"{indent}{key}:{tail}{eol}"] + block + lines[end:]


def patch_scalars(text: str, patch: dict) -> Optional[str]:
    """只改 patch 裡那幾個純量的值，其餘（尤其是註解）一個字都不動。

    repo 裡的 profile 用註解記錄實測數據（例如 1366x768 那份的繩點調整過程），
    merge_into 走 safe_dump 會把它們全部洗掉。清單值（巡邏點、buff）只換掉
    那一個區塊，區塊裡的註解會跟著舊清單一起走。做不到就回 None 讓呼叫端改用
    merge_into：flow 風格的區段、檔案裡沒有的 key 都算。改完一定重新 safe_load，必須跟 _deep_merge(原本, patch)
    完全相等才算數——任何沒想到的 YAML 寫法都只會退回舊路，不會寫壞檔案。
    """
    try:
        original = yaml.safe_load(text) if text.strip() else {}
    except yaml.YAMLError:
        return None
    if not isinstance(original, dict):
        return None
    lines = text.splitlines(keepends=True)
    for path, value in _leaves(patch):
        if _lookup(original, path) == value:
            continue                                # 值沒變（例如每次都帶的 name）就不碰
        idx = _find_key_line(lines, path)
        if idx is None:
            return None
        lines = _replace_node(lines, idx, path[-1], value)
        if lines is None:
            return None
    result = "".join(lines)
    try:
        ok = yaml.safe_load(result) == _deep_merge(original, patch)
    except yaml.YAMLError:
        return None
    return result if ok else None


def _backup(path: str, backup_once: set) -> Optional[str]:
    """註解要掉之前留一份 '<檔名>.bak'。已經有就不蓋：那份才是還有註解的原檔，
    下一次開控制台再存一次的話，蓋過去的會是已經沒註解的版本。

    回傳 .bak 路徑給呼叫端寫日誌；同一個 session 只回報一次。
    """
    key = os.path.normcase(os.path.abspath(path))
    if key in backup_once:
        return None
    backup_once.add(key)
    bak = path + ".bak"
    if not os.path.exists(bak):
        shutil.copyfile(path, bak)
    return bak


def _comment_lines(text: str) -> int:
    return sum(1 for line in text.splitlines() if "#" in line)


def write_patch(path: str, patch: dict, backup_once: set) -> Optional[str]:
    """把 patch 寫進 path：能就地改就保留註解，否則退回 merge_into。

    只要這次寫入會少掉註解（清單區塊換掉、或整份重寫），先留一份
    '<檔名>.bak'（見 _backup）。回傳 .bak 路徑讓呼叫端寫進日誌，沒有就回 None。
    patch 是空的就完全不碰檔案（不會因此建出空的 local.yaml）。
    """
    if not patch:
        return None
    made = None
    if os.path.exists(path):
        # newline='' 保留原本的換行字元：只改一行就不該把整個檔案換成 CRLF
        with open(path, "r", encoding="utf-8", newline="") as f:
            text = f.read()
        new = patch_scalars(text, patch)
        if new is not None:
            if new != text:
                if _comment_lines(new) < _comment_lines(text):
                    made = _backup(path, backup_once)
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(new)
            return made
        if _comment_lines(text):
            made = _backup(path, backup_once)
    merge_into(path, patch)
    return made
