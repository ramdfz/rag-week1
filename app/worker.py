from uvicorn.workers import UvicornWorker


class NoServerHeaderUvicornWorker(UvicornWorker):
    CONFIG_KWARGS = {"server_header": False}
