defmodule ContractLab.VendorSourceTest do
  use ExUnit.Case, async: true

  @contracts Path.expand("../..", __DIR__)
  @repository Path.dirname(@contracts)
  @revision "1ca69d8bf3731719266da2cb900edcdb7b45a0bb"

  defp manifest do
    @contracts |> Path.join("SOURCE.json") |> File.read!() |> JSON.decode!()
  end

  test "the import identifies its retrievable immutable source" do
    source = manifest()
    assert source["repository"] == "https://github.com/thenvoi/contract-spec"
    assert source["revision"] == @revision
  end

  test "every mapped published file is covered by its actual byte digest" do
    entries = manifest()["original_files"]
    targets = Enum.map(entries, fn {_, entry} -> entry["target"] end)
    assert length(targets) == length(Enum.uniq(targets))

    for {name, entry} <- entries do
      assert is_binary(entry["sha256"]) and Regex.match?(~r/\A[0-9a-f]{64}\z/, entry["sha256"]),
             name

      path = Path.expand(entry["target"], @repository)
      assert String.starts_with?(path, @repository <> "/"), name
      assert File.regular?(path), "Missing published target: #{name}"

      actual = :crypto.hash(:sha256, File.read!(path)) |> Base.encode16(case: :lower)
      assert entry["published_sha256"] == actual, "Published digest mismatch: #{name}"
    end
  end
end
