"""Tiny demo package with an intentional bug for pytest."""


def divide(a: float, b: float) -> float:
    # Intentionally wrong: should return a / b
    return a * b
