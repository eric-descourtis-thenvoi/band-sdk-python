defmodule HeaderSpikes.Graphemes do
  @moduledoc "Unicode 18.0 extended grapheme boundaries."

  @doc "The Unicode version of the segmentation data in use, never a hard-coded claim."
  def version, do: Unicode.version() |> Tuple.to_list() |> Enum.join(".")

  def count(text) when is_binary(text), do: length(clusters(text))

  @doc "Boundary offsets measured in Unicode scalar values, including start and end."
  def boundaries(text) when is_binary(text) do
    {_offset, positions} =
      Enum.reduce(clusters(text), {0, [0]}, fn cluster, {offset, positions} ->
        next = offset + length(String.codepoints(cluster))
        {next, [next | positions]}
      end)

    Enum.reverse(positions)
  end

  defp clusters(""), do: []

  defp clusters(text) do
    unless String.valid?(text), do: raise(ArgumentError, "invalid UTF-8")
    Unicode.String.split(text, break: :grapheme)
  end
end
