# Two checks of one function, sort in tinysort.py, on every permutation of 0..n-1 for n <= 6.
# `sorted` holds. `broken` rotates its output by one place: it fails, and its verdict is kept.
{ pkgs, v }:
let
  common = {
    property = "sort(xs) returns the elements of xs in ascending order";
    model = "tinysort.sort on permutations of distinct integers 0..n-1, n <= 6 (not duplicates, not other types)";
    checker = pkgs.python3;
    command = "python3 ${./check_sort.py}";
    scope.lengths = [ 0 1 2 3 4 5 6 ];
  };
in
{
  sorted = v.check (common // { name = "sorted"; subject = ./sorted/tinysort.py; });
  broken = v.check (common // { name = "broken"; subject = ./broken/tinysort.py; });
}
