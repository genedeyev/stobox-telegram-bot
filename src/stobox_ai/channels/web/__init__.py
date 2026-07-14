"""Web / HTTP channel adapter.

Doubles as the backend for a website chat widget and any HTTP integration. The
core (:meth:`WebChannel.chat`) is framework-free and fully testable offline; an
optional ASGI app factory exposes it over HTTP when a web framework is present.
"""

from .adapter import WebChannel, create_app

__all__ = ["WebChannel", "create_app"]
