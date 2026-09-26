import sys

from .cli import main
from .ui import pause_if_own_console

code = main()
pause_if_own_console()
sys.exit(code)
