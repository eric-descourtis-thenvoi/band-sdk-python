defmodule ContractLab.FieldKindsTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine}

  defp compile(type) do
    bytes =
      ContractLab.TestClock.ensure("""
      ---
      name: field-kinds
      description: One payload field.
      engine: 0.2.0-lab.1
      roles: [sender, receiver]
      ---
      contract @infinity => expired
      type value
        item: #{type}
      state new
        sender -> receiver: value() => complete
      end complete
      end expired
      """)

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    })
  end

  defp admit(type, value) do
    assert {:ok, compiled} = compile(type)

    {:ok, session} =
      Engine.new(compiled,
        session_id: "s",
        bindings: %{"sender" => "a", "receiver" => "b"},
        now: 0,
        inactivity: :accepted,
        max_messages: 2,
        room_policy: :unchecked
      )

    {decision, _} =
      Engine.step(
        session,
        %{"id" => "v", "session" => "s", "kind" => "value", "payload" => %{"item" => value}},
        actor: "a",
        now: 0
      )

    decision
  end

  test "an integer range may include negatives" do
    assert {:ok, compiled} = compile("integer(-5..5)")
    assert %{kind: :integer, min: -5, max: 5} = compiled.types["value"].fields["item"]
    assert admit("integer(-5..5)", -5).status == :accepted
    assert admit("integer(-5..5)", 6).reason == :payload
    assert {:error, %{code: :type}} = compile("integer(5..-5)")
  end

  test "uuid is a lowercase canonical uuid" do
    assert {:ok, compiled} = compile("uuid")
    assert %{kind: :uuid} = compiled.types["value"].fields["item"]
    assert admit("uuid", "00000000-0000-0000-0000-000000000000").status == :accepted
    refused = admit("uuid", "00000000-0000-0000-0000-00000000000G")
    assert refused.reason == :payload
    assert refused.detail.constraint == "type"
    assert refused.detail.expected == "uuid"
  end

  test "timestamp and duration are millisecond integers inside the declared bounds" do
    assert admit("timestamp(0..1000)", 1000).status == :accepted
    assert admit("timestamp(0..1000)", -1).reason == :payload
    assert {:ok, compiled} = compile("duration(1s..1h)")
    assert %{kind: :duration, min: 1000, max: 3_600_000} = compiled.types["value"].fields["item"]
    assert admit("duration(1s..1h)", 60_000).status == :accepted
    assert admit("duration(1s..1h)", 500).reason == :payload
    assert {:error, %{code: :type}} = compile("duration(0s..1s)")
    assert {:error, %{code: :type}} = compile("duration(2h..1s)")
  end

  test "decimal is a canonical decimal string inside the declared scale and range" do
    assert {:ok, compiled} = compile("decimal(-1.50..2.00)")

    assert %{kind: :decimal, min: "-1.50", max: "2.00", scale: 2} =
             compiled.types["value"].fields["item"]

    assert admit("decimal(-1.50..2.00)", "-1.5").status == :accepted
    assert admit("decimal(-1.50..2.00)", "2").status == :accepted
    assert admit("decimal(-1.50..2.00)", "2.0").detail.constraint == "type"
    assert admit("decimal(-1.50..2.00)", "2.001").detail.constraint == "type"
    assert admit("decimal(-1.50..2.00)", "3").detail.constraint == "range"
    assert admit("decimal(-1.50..2.00)", 2).detail.expected == "decimal"
    assert {:error, %{code: :type}} = compile("decimal(2..1)")
    assert {:error, %{code: :type}} = compile("decimal(-0..1)")
  end

  test "bytes is standard base64 of a declared decoded length" do
    assert admit("bytes(1..2)", "YQ==").status == :accepted
    assert admit("bytes(1..2)", "YWI=").status == :accepted
    assert admit("bytes(1..2)", "YWJj").detail.constraint == "bytes"
    assert admit("bytes(1..2)", "YQ").detail.expected == "bytes"
    assert admit("bytes(1..2)", "0a").detail.expected == "bytes"
    assert admit("bytes(0..1)", "").status == :accepted
  end

  test "a list has a bounded length and one non-list element type" do
    assert {:ok, compiled} = compile("list(1..3, enum(approve|revise))")

    assert %{kind: :list, min: 1, max: 3, element: %{kind: :enum, values: ["approve", "revise"]}} =
             compiled.types["value"].fields["item"]

    assert admit("list(1..3, enum(approve|revise))", ["approve", "revise"]).status == :accepted
    assert admit("list(1..3, enum(approve|revise))", []).detail.constraint == "range"
    assert admit("list(1..3, enum(approve|revise))", ["nope"]).detail.constraint == "enum"
    assert admit("list(0..2, uuid)", ["00000000-0000-0000-0000-000000000000"]).status == :accepted
    assert {:error, %{code: :type}} = compile("list(0..1, list(0..1, uuid))")
    assert {:error, %{code: :type}} = compile("list(0..257, boolean)")
  end

  test "timestamptz keeps its offset and checks the instant" do
    assert {:ok, compiled} = compile("timestamptz(0..3600000)")
    assert %{kind: :timestamptz, min: 0, max: 3_600_000} = compiled.types["value"].fields["item"]
    assert admit("timestamptz(0..3600000)", "1970-01-01T00:00:00.000Z").status == :accepted
    assert admit("timestamptz(0..3600000)", "1970-01-01T04:00:00.000+04:00").status == :accepted
    assert admit("timestamptz(0..3600000)", "1970-01-01T01:30:00.000+01:00").status == :accepted

    for bad <- [
          "1970-01-01T00:00:00Z",
          "1970-01-01T00:00:00.000+00:00",
          "1970-01-01T00:00:00.000",
          "1970-02-31T00:00:00.000Z",
          "1970-01-01T00:00:00.000+18:30"
        ] do
      refused = admit("timestamptz(0..3600000)", bad)
      assert refused.detail.expected == "timestamptz", bad
    end

    assert admit("timestamptz(0..3600000)", "1970-01-01T02:00:00.000Z").detail.constraint ==
             "range"
  end

  test "a decimal followed by a line feed is a typed payload refusal, not a crash" do
    for value <- ["1\n", "1.5\n", "-1\n", "1\n\n"] do
      refused = admit("decimal(-2..2.50)", value)
      assert refused.reason == :payload, inspect(value)
      assert refused.detail.constraint == "type", inspect(value)
      assert refused.detail.expected == "decimal", inspect(value)
    end

    refute ContractLab.Decimal.literal?("1\n")
    assert admit("decimal(-2..2.50)", "1").status == :accepted
  end

  defp timed_session(type, max_bytes) do
    bytes = """
    ---
    name: timed-field
    description: One timed payload field.
    engine: 0.2.0-lab.1
    roles: [sender, receiver]
    ---
    contract @infinity => expired
    type value
      item: #{type}
    state new
      sender -> receiver: value() @5m => complete | late
    end complete
    end late
    end expired
    """

    {:ok, compiled} =
      ContractLab.compile_source(%{
        "profile" => "band-contract/file-source/1",
        "format" => "frontmatter",
        "content" => bytes,
        "sha256" => Canonical.hash(bytes)
      })

    Engine.new(compiled,
      session_id: "s",
      bindings: %{"sender" => "a", "receiver" => "b"},
      now: 0,
      inactivity: :accepted,
      max_messages: 2,
      max_bytes: max_bytes,
      room_policy: :unchecked
    )
  end

  defp reply_bytes(item),
    do:
      byte_size(
        Canonical.encode(%{
          "id" => "a",
          "session" => "s",
          "kind" => "value",
          "payload" => %{"item" => item}
        })
      )

  test "completion capacity sizes a signed integer range by its value nearest zero" do
    for {type, shortest} <- [
          {"integer(0..0)", 0},
          {"integer(-9007199254740991..0)", 0},
          {"integer(-9007199254740991..9007199254740991)", 0},
          {"integer(-9007199254740991..-3)", -3},
          {"integer(7..9007199254740991)", 7}
        ] do
      exact = reply_bytes(shortest)
      assert {:ok, _} = timed_session(type, exact), type
      assert {:error, :completion_size} = timed_session(type, exact - 1), type
    end
  end

  test "uuid and timestamptz refuse a trailing line feed, a second spelling of the value" do
    uuid = "00000000-0000-0000-0000-000000000000"
    assert admit("uuid", uuid).status == :accepted
    assert admit("uuid", uuid <> "\n").detail.expected == "uuid"
    zoned = "1970-01-01T00:00:00.000Z"
    assert admit("timestamptz(0..3600000)", zoned).status == :accepted
    assert admit("timestamptz(0..3600000)", zoned <> "\n").detail.expected == "timestamptz"

    assert admit("timestamptz(0..3600000)", "1970-01-01T04:00:00.000+04:00\n").detail.expected ==
             "timestamptz"
  end

  test "a timestamptz bound past year 9999 starts a session instead of crashing" do
    type = "timestamptz(300000000000000..400000000000000)"
    zoned = reply_bytes("1970-01-01T00:00:00.000Z")
    assert {:ok, _} = timed_session(type, zoned)
    assert {:error, :completion_size} = timed_session(type, zoned - 1)
    assert {:ok, _} = timed_session("timestamptz(0..3600000)", zoned)
    assert admit(type, "9999-12-31T23:59:59.999Z").detail.constraint == "range"
  end
end
