defmodule ContractLab.Decimal do
  @moduledoc false

  # A decimal payload is a string, because the canonical JSON profile has no
  # floats. The spelling is canonical: no exponent, no leading zero, no trailing
  # fractional zero, and no negative zero. A bound may keep trailing fractional
  # zeros; those zeros declare the scale.
  @safe 9_007_199_254_740_991
  # \A and \z: `$` would also match before a final line feed.
  @literal ~r/\A-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\z/

  @spec literal?(term()) :: boolean()
  def literal?(text) when is_binary(text), do: Regex.match?(@literal, text) and scale(text) <= 18
  def literal?(_), do: false

  @spec scale(String.t()) :: non_neg_integer()
  def scale(text) do
    case String.split(text, ".", parts: 2) do
      [_] -> 0
      [_, fraction] -> String.length(fraction)
    end
  end

  @spec parts(String.t()) :: {integer(), non_neg_integer()}
  def parts(text) do
    negative = String.starts_with?(text, "-")
    body = if negative, do: String.trim_leading(text, "-"), else: text

    {whole, fraction} =
      case String.split(body, ".", parts: 2) do
        [whole] -> {whole, ""}
        [whole, fraction] -> {whole, fraction}
      end

    coefficient = String.to_integer(whole <> fraction)
    {if(negative, do: -coefficient, else: coefficient), String.length(fraction)}
  end

  @spec compare(String.t(), String.t()) :: :lt | :eq | :gt
  def compare(left, right) do
    {left_n, left_scale} = parts(left)
    {right_n, right_scale} = parts(right)
    scaled_left = left_n * pow10(right_scale)
    scaled_right = right_n * pow10(left_scale)

    cond do
      scaled_left < scaled_right -> :lt
      scaled_left > scaled_right -> :gt
      true -> :eq
    end
  end

  @spec canonical?(term(), non_neg_integer()) :: boolean()
  def canonical?(text, scale) when is_binary(text) and is_integer(scale) do
    literal?(text) and scale(text) <= scale and not trailing_zero?(text) and
      not negative_zero?(text)
  end

  def canonical?(_, _), do: false

  # `{:ok, scale}` when both bounds are literals, the wider scale is at most 18,
  # the numeric minimum does not exceed the maximum, and both fit a safe integer
  # once written at that scale.
  @spec bounds(term(), term()) :: {:ok, non_neg_integer()} | :error
  def bounds(min, max) do
    cond do
      not (literal?(min) and literal?(max)) ->
        :error

      negative_zero?(min) or negative_zero?(max) ->
        :error

      true ->
        scale = max(scale(min), scale(max))
        left = rescale(min, scale)
        right = rescale(max, scale)

        if left <= right and left >= -@safe and right <= @safe,
          do: {:ok, scale},
          else: :error
    end
  end

  @spec canonical(String.t()) :: String.t()
  def canonical(text) do
    {coefficient, scale} = parts(text)
    sign = if coefficient < 0, do: "-", else: ""
    digits = coefficient |> abs() |> Integer.to_string()
    digits = String.pad_leading(digits, scale + 1, "0")
    {whole, fraction} = String.split_at(digits, String.length(digits) - scale)
    fraction = String.trim_trailing(fraction, "0")
    body = if fraction == "", do: whole, else: whole <> "." <> fraction
    sign <> body
  end

  defp rescale(text, scale) do
    {coefficient, text_scale} = parts(text)
    coefficient * pow10(scale - text_scale)
  end

  defp trailing_zero?(text) do
    case String.split(text, ".", parts: 2) do
      [_, fraction] -> String.ends_with?(fraction, "0")
      _ -> false
    end
  end

  defp negative_zero?(text), do: String.starts_with?(text, "-") and match?({0, _}, parts(text))

  defp pow10(n) when n <= 0, do: 1
  defp pow10(n), do: 10 * pow10(n - 1)
end
