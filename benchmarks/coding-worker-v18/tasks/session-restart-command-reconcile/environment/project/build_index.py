from pathlib import Path
from index_builder import build_index

build_index(Path(__file__).resolve().parent)
