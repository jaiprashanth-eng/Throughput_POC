from rest_framework.status import HTTP_400_BAD_REQUEST, HTTP_404_NOT_FOUND


class DirectError(Exception):
    status_code = HTTP_400_BAD_REQUEST

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class DirectNotFound(DirectError):
    status_code = HTTP_404_NOT_FOUND
