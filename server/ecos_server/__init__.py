"""server.ecos_server package exports."""


def serve(*args, **kwargs):
    from .main import serve as _serve

    return _serve(*args, **kwargs)


__all__ = ["serve"]
