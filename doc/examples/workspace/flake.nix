{
  description = "The toy workspace: two projects, two notes, one flake";

  inputs.nixsci.url = "git+file:///home/pkd/code/nixsci?ref=main";
  inputs.nixpkgs.follows = "nixsci/nixpkgs";

  outputs = inputs: inputs.nixsci.lib.workspace { inherit inputs; root = ./.; };
}
