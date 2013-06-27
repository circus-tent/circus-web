from circusweb.controller import Controller
from tornado import gen

_CONTROLLER = None


def get_controller():
    return _CONTROLLER


def set_controller(controller):
    global _CONTROLLER
    _CONTROLLER = controller


def disconnect_from_circus(endpoint):
    controller = get_controller()
    if controller is not None:
        controller.disconnect(endpoint)
        return True
    return False


@gen.coroutine
def connect_to_circus(loop, endpoint, ssh_server=None):
    if get_controller() is None:
        controller = Controller(loop, ssh_server=ssh_server)
        set_controller(controller)
    else:
        controller = get_controller()

    yield gen.Task(controller.connect, endpoint)
    set_controller(controller)
