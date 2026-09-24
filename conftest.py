"""Делает каталог проекта импортируемым при запуске pytest из любой папки."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
