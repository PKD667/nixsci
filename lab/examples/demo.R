suppressMessages({
  library(dplyr)
  library(labr)
})

loss <- lab_data("demo", "loss") |> collect()
runs <- lab_manifests("demo")

final <- loss |>
  group_by(run) |>
  slice_max(epoch, n = 1) |>
  ungroup() |>
  left_join(runs, by = "run") |>
  select(run, seed = seed.x, epsilon = param.epsilon, final_loss = value)

write.csv(final, lab_out("final.csv"), row.names = FALSE)
cat(nrow(final), "runs summarised\n")
