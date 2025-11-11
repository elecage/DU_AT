HANDLER_NAME_MAP = {}

def register_handler(name):
    def wrapper(func):
        HANDLER_NAME_MAP[name] = func
        return func
    return wrapper
