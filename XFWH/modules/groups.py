import os, json
from config import DATA_DIR

GROUP_FILE = os.path.join(DATA_DIR, "groups.json")
groups_data = {}

def load_groups():
    global groups_data
    if os.path.exists(GROUP_FILE):
        with open(GROUP_FILE, "r", encoding="utf-8") as f:
            groups_data = json.load(f)
    else:
        groups_data = {}

def save_groups():
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(GROUP_FILE, "w", encoding="utf-8") as f:
        json.dump(groups_data, f, ensure_ascii=False, indent=2)

def add_group(group_id, group_name):
    groups_data[str(group_id)] = group_name
    save_groups()

def remove_group(group_id):
    groups_data.pop(str(group_id), None)
    save_groups()

def list_groups():
    return groups_data
