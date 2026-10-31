defmodule ContractLab.PublicationPathsTest do
  use ExUnit.Case, async: true

  @contracts Path.expand("../..", __DIR__)
  @repository Path.dirname(@contracts)
  @home ~r{(?:[/](?:home|Users)[/]|[~][/]|[A-Za-z]:[\\/]Users[\\/])[^\s/"'<>]+}
  @workspace ~r{[/](?:tv|band-codex-engineers-workspace)[/][^\s"'<>]+}
  @temporary ~r{[/]tmp[/][^\s"'<>]+}

  test "the public import contains no personal home or local evidence paths" do
    {inventory, status} =
      System.cmd("git", ["ls-files", "-z", "--", "contracts"], cd: @repository)

    assert status == 0

    sites =
      for relative <- String.split(inventory, <<0>>, trim: true),
          {line, number} <-
            @repository
            |> Path.join(relative)
            |> File.read!()
            |> String.split("\n")
            |> Enum.with_index(1),
          Regex.match?(@home, line) or Regex.match?(@workspace, line) or
            (String.contains?(relative, "/evidence/") and Regex.match?(@temporary, line)),
          do: "#{relative}:#{number}"

    assert sites == [], "Nonportable publication paths at " <> Enum.join(sites, ", ")
  end

  test "path-redacted evidence records its new publication bytes" do
    source = @contracts |> Path.join("SOURCE.json") |> File.read!() |> JSON.decode!()
    redactions = get_in(source, ["publication_redactions", "host_paths", "files"])
    assert is_map(redactions) and map_size(redactions) > 0

    for {relative, entry} <- redactions do
      bytes = @contracts |> Path.join(relative) |> File.read!()
      digest = :crypto.hash(:sha256, bytes) |> Base.encode16(case: :lower)
      assert entry["published_sha256"] == digest, relative
      refute entry["before_sha256"] == digest, relative
    end
  end
end
