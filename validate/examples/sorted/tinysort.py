"""Insertion sort. Subject of the `sorted` example."""


def sort(xs):
    out = []
    for x in xs:
        i = len(out)
        while i > 0 and out[i - 1] > x:
            i -= 1
        out.insert(i, x)
    return out
