# -*- coding: utf-8 -*-
"""支持 python -m pdfreader 调用。"""
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
