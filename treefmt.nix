{
  # Not '.git/config': in a git worktree or a jj workspace '.git' is a file, so treefmt would walk up to and format the
  #  enclosing main checkout instead.
  projectRootFile = "flake.nix";
  programs = {
    prettier.enable = true;
    nixfmt.enable = true;
    yamlfmt.enable = true;
    taplo.enable = true;
    shfmt.enable = true;
    ruff-check.enable = true;
    ruff-format = {
      enable = true;
      lineLength = 120;
      priority = 1;
    };
  };
}
