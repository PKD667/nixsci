view_root <- function() {
  view <- Sys.getenv("NIX_LAB_VIEW")
  if (!nzchar(view)) {
    stop("not inside a nixsci analysis: NIX_LAB_VIEW is not set", call. = FALSE)
  }
  view
}

dirs_in <- function(path) list.dirs(path, recursive = FALSE, full.names = FALSE)

#' Open one alias declared under `use` in the analysis.
#'
#' The only way to reach data: an alias that the spec did not declare is an error, so a script
#' reads nothing its spec does not name. `alias$dataset` is a lazy Arrow dataset over exactly the
#' locked runs.
#' @param alias name on the left of an entry in [use]
use <- function(alias) {
  declared <- dirs_in(view_root())
  if (!alias %in% declared) {
    stop(sprintf("'%s' is not declared in [use]; declared: %s", alias,
                 paste(declared, collapse = ", ")), call. = FALSE)
  }
  structure(list(alias = alias, root = file.path(view_root(), alias)), class = "nixsci_use")
}

datasets_of <- function(x) dirs_in(.subset2(x, "root"))

#' @export
`$.nixsci_use` <- function(x, name) {
  have <- datasets_of(x)
  if (!name %in% have) {
    stop(sprintf("'%s' has no dataset '%s'; it has: %s", .subset2(x, "alias"), name,
                 paste(have, collapse = ", ")), call. = FALSE)
  }
  arrow::open_dataset(file.path(.subset2(x, "root"), name))
}

#' @export
print.nixsci_use <- function(x, ...) {
  cat("<nixsci use ", .subset2(x, "alias"), ": ", paste(datasets_of(x), collapse = ", "), ">\n",
      sep = "")
  invisible(x)
}

#' One row per locked run: run, seed, state, target, started, ended, input_id, replicate, and
#' `params`, a list column holding each run's parameters.
#' @param x an alias opened with use()
runs <- function(x) {
  rows <- jsonlite::fromJSON(file.path(.subset2(x, "root"), "runs.json"), simplifyVector = FALSE)
  text <- function(field) {
    vapply(rows, function(r) if (is.null(r[[field]])) NA_character_ else as.character(r[[field]]),
           character(1))
  }
  frame <- data.frame(
    run = text("run"), seed = suppressWarnings(as.integer(text("seed"))), state = text("state"),
    target = text("target"), started = text("started"), ended = text("ended"),
    input_id = text("input_id"), replicate = suppressWarnings(as.integer(text("replicate"))),
    stringsAsFactors = FALSE
  )
  frame$params <- lapply(rows, function(r) r$params)
  frame
}

#' `run` plus one column per parameter (NA where a run did not set it).
#' @param x an alias opened with use()
params <- function(x) {
  frame <- runs(x)
  keys <- unique(unlist(lapply(frame$params, names)))
  wide <- data.frame(run = frame$run, stringsAsFactors = FALSE)
  for (key in keys) {
    wide[[key]] <- sapply(frame$params, function(p) if (is.null(p[[key]])) NA else p[[key]])
  }
  wide
}

#' A path inside this pipeline's output directory.
out <- function(...) {
  root <- Sys.getenv("NIX_LAB_OUT")
  if (!nzchar(root)) stop("not inside a nixsci analysis: NIX_LAB_OUT is not set", call. = FALSE)
  file.path(root, ...)
}
