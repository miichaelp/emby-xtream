import os
import sys

# The plugin folder has no __init__.py (Dispatcharr loads it as a namespace package),
# so its parent goes on the path and "strm_sync" imports the same way here.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
