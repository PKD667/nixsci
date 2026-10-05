#' A compacted dataset as a lazy Arrow dataset (all runs; `run`, `seed`, `time` columns included).
#' Pipe it through dplyr verbs and `collect()`.
#' @param app experiment name
#' @param dataset dataset name declared in the spec's [data.<name>] table
#' @param root compacted data root (default: $NIX_LAB_DATA)
lab_data <- function(app, dataset, root = Sys.getenv("NIX_LAB_DATA")) {
  stopifnot(nzchar(root))
  arrow::open_dataset(file.path(root, app, dataset))
}

#' One row per run of an app: id, state, seed, target, times, and its parameters as `param.<name>` columns.
#' @param root raw runs root (default: $NIX_LAB_RUNS)
lab_manifests <- function(app, root = Sys.getenv("NIX_LAB_RUNS")) {
  stopifnot(nzchar(root))
  files <- Sys.glob(file.path(root, app, "*", "manifest.json"))
  rows <- lapply(files, function(f) {
    m <- jsonlite::fromJSON(f)
    params <- m$params
    names(params) <- paste0("param.", names(params))
    base <- data.frame(
      run = m$run, state = m$state, seed = if (is.null(m$seed)) NA_integer_ else m$seed,
      target = m$target, started = m$started, ended = m$ended, stringsAsFactors = FALSE
    )
    if (length(params)) base <- cbind(base, as.data.frame(params, stringsAsFactors = FALSE))
    base
  })
  if (!length(rows)) return(data.frame())
  do.call(rbind, rows)
}

#' Path inside this pipeline's output directory (default: $NIX_LAB_OUT).
lab_out <- function(..., root = Sys.getenv("NIX_LAB_OUT")) {
  stopifnot(nzchar(root))
  file.path(root, ...)
}
