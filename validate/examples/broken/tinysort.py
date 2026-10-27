"""A sort that rotates its output by one place. Subject of the `broken` example: it must fail."""


def sort(xs):
    s = sorted(xs)
    return s[1:] + s[:1]
