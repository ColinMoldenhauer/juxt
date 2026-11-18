# juxt-notebook

The [juxt](https://github.com/ColinMoldenhauer/juxt) Jupyter widget without the Qt desktop app.

```bash
pip install juxt-notebook
```

```python
from juxt.notebook import Viewer
Viewer("config.yaml")
```

This package installs the same `juxt` Python package as `pip install juxt`, but without PySide6. The `juxt` desktop command is not included. If you want the desktop app as well, install `juxt[notebook]` instead, and don't install both packages.

See the [notebook documentation](https://github.com/ColinMoldenhauer/juxt/blob/main/docs/notebook.md).
