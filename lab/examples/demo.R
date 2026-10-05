suppressMessages({
  library(dplyr)
  library(nixsci)
})

demo <- use("demo")

final <- demo$loss |>
  collect() |>
  group_by(run) |>
  slice_max(epoch, n = 1) |>
  ungroup() |>
  left_join(params(demo), by = "run") |>
  select(run, seed, epsilon, final_loss = value)

write.csv(final, out("final.csv"), row.names = FALSE)
cat(nrow(final), "runs summarised\n")
