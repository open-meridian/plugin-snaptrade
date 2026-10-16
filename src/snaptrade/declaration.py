"""What this version declares beside its roles (W8.1, W4.1): from contract
v25 the whole of what it says about itself.

Built from the plugin's code and used twice: `__main__` registers with it,
and `meridian plugin upload` reads the same one from the built image
(pyproject.toml's `[tool.meridian] declaration`), with the documentation's
texts beside it. A version registers exactly the declaration it was uploaded
with, or its sidecar refuses it naming the first difference, so:

- its settings (settings.py), its secret ones among them by name only;
- its interface: its pages, with the levels each serves, and the tools their
  typed routes make (page.py), served on a port fixed here, never read from
  the environment;
- that it names accounts by SnapTrade's identifiers, which an admin of the
  plugin links to the deployment's accounts (W6.4);
- what SnapTrade sends and it does not carry, by name (not_carried.py);
- the storage it asks for at the edge, with its three kinds of raw record
  (kinds.py);
- and the vendor's documentation, the Markdown in `docs/` (`/plugin/docs` in
  its image), which a deployment admin reads on its Documentation tab before
  launching it.

Nothing about what it supports, which its roles say.
"""

from __future__ import annotations

import meridian
from meridian.declaration import Declaration

from .kinds import STORAGE
from .not_carried import NOT_CARRIED
from .page import TITLE, pages
from .settings import DECLARED

#: The port its pages are served on, on loopback, where its sidecar forwards.
PORT = 8000

DECLARATION = Declaration(
    settings=DECLARED,
    not_carried=NOT_CARRIED,
    storage=STORAGE,
    interface=meridian.Interface(port=PORT, title=TITLE, pages=pages),
    reads_external_accounts=True,
    documentation="docs",
)
