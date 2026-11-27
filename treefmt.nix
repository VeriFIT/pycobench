{
  # Not '.git/config': in a git worktree or a jj workspace '.git' is a file, so treefmt would walk up to and format the
  #  enclosing main checkout instead.
  projectRootFile = "flake.nix";
  programs = {
    prettier.enable = true;
    nixfmt.enable = true;
    yamlfmt.enable = true;
    taplo.enable = true;
    # treefmt-nix no longer sets shfmt options by default; keep the established style.
    shfmt = {
      enable = true;
      indent_size = 2;
      simplify = true;
    };
    ruff-check.enable = true;
    ruff-format = {
      enable = true;
      lineLength = 120;
      priority = 1;
    };
  };
}
