import assert from "node:assert/strict";
import test from "node:test";
import {
  CanonicalJsonOperationalError,
  CanonicalJsonValueError,
  canonicalizeJsonValue,
} from "../src/index.mjs";

function assertInvalid(value) {
  assert.throws(
    () => canonicalizeJsonValue(value),
    (error) =>
      error instanceof CanonicalJsonValueError &&
      error.code === "CANONICAL_JSON_VALUE_INVALID" &&
      error.message === "CANONICAL_JSON_VALUE_INVALID",
  );
}

test("sorts object keys by UTF-16 code unit and preserves array order", () => {
  const value = {
    "\ue000": 4,
    "😀": 3,
    "𐀀": 2,
    a: [3, 2, 1],
  };
  assert.equal(
    canonicalizeJsonValue(value),
    '{"a":[3,2,1],"𐀀":2,"😀":3,"":4}',
  );
});

test("uses ECMAScript string escaping and emits no framing bytes", () => {
  const result = canonicalizeJsonValue({ control: "\u0000\n\"\\", text: "中文" });
  assert.equal(result, '{"control":"\\u0000\\n\\\"\\\\","text":"中文"}');
  assert.equal(result.startsWith("\ufeff"), false);
  assert.equal(result.endsWith("\n"), false);
  assert.equal(result.includes(": "), false);
});

test("preserves U+2028/U+2029 and does not normalize Unicode", () => {
  const separators = "line\u2028paragraph\u2029";
  assert.equal(canonicalizeJsonValue(separators), `"${separators}"`);

  const nfc = "é";
  const nfd = "e\u0301";
  assert.notEqual(nfc, nfd);
  assert.equal(canonicalizeJsonValue(nfc), '"é"');
  assert.equal(canonicalizeJsonValue(nfd), '"é"');
  assert.notEqual(canonicalizeJsonValue(nfc), canonicalizeJsonValue(nfd));
});

test("accepts only safe integers and normalizes negative zero", () => {
  assert.equal(canonicalizeJsonValue(-0), "0");
  assert.equal(canonicalizeJsonValue(Number.MAX_SAFE_INTEGER), "9007199254740991");
  assert.equal(canonicalizeJsonValue(Number.MIN_SAFE_INTEGER), "-9007199254740991");
  for (const value of [
    Number.NaN,
    Number.POSITIVE_INFINITY,
    Number.NEGATIVE_INFINITY,
    1.5,
    Number.MAX_SAFE_INTEGER + 1,
  ]) {
    assertInvalid(value);
  }
});

test("rejects values outside the JSON data model", () => {
  for (const value of [undefined, 1n, Symbol("value"), () => {}]) {
    assertInvalid(value);
  }
  assertInvalid({ missing: undefined });
  assertInvalid([undefined]);
});

test("escapes lone UTF-16 surrogates in values and embedded positions", () => {
  const cases = [
    ["\ud800", '"\\ud800"'],
    ["\udabc", '"\\udabc"'],
    ["\udfff", '"\\udfff"'],
    [`before${"\ud800"}middle${"\udfff"}after`, '"before\\ud800middle\\udfffafter"'],
    [`${"\udfff"}${"\ud800"}`, '"\\udfff\\ud800"'],
  ];
  for (const [value, expected] of cases) {
    assert.equal(canonicalizeJsonValue(value), expected);
  }
});

test("preserves every valid surrogate-pair boundary as a real scalar", () => {
  const cases = [
    [0xd800, 0xdc00, 0x10000],
    [0xd800, 0xdfff, 0x103ff],
    [0xdbff, 0xdc00, 0x10fc00],
    [0xdbff, 0xdfff, 0x10ffff],
  ];
  for (const [high, low, codePoint] of cases) {
    const pair = String.fromCharCode(high, low);
    const scalar = String.fromCodePoint(codePoint);
    assert.equal(pair, scalar);
    assert.equal(canonicalizeJsonValue(pair), JSON.stringify(scalar));
    assert.equal(canonicalizeJsonValue(pair).includes("\\u"), false);
  }
});

test("keeps lone, replacement, and literal-escape keys collision-free", () => {
  const loneHighKey = String.fromCharCode(0xd800);
  const replacementKey = String.fromCodePoint(0xfffd);
  const literalEscapeKey = String.raw`\ud800`;
  const canonicalText = canonicalizeJsonValue({
    [loneHighKey]: "lone",
    [replacementKey]: "replacement",
    [literalEscapeKey]: "literal-escape",
  });
  const parsed = JSON.parse(canonicalText);

  assert.equal(Object.getPrototypeOf(parsed), Object.prototype);
  assert.equal(Object.keys(parsed).length, 3);
  assert.equal(Object.hasOwn(parsed, loneHighKey), true);
  assert.equal(Object.hasOwn(parsed, replacementKey), true);
  assert.equal(Object.hasOwn(parsed, literalEscapeKey), true);
  assert.equal(parsed[loneHighKey], "lone");
  assert.equal(parsed[replacementKey], "replacement");
  assert.equal(parsed[literalEscapeKey], "literal-escape");
  assert.equal(Object.hasOwn(Object.prototype, loneHighKey), false);
  assert.equal(Object.hasOwn(Object.prototype, literalEscapeKey), false);
});

test("keeps lone surrogates distinct from U+FFFD deterministically", () => {
  const replacementCharacter = String.fromCodePoint(0xfffd);
  const loneHigh = canonicalizeJsonValue("\ud800");
  const loneLow = canonicalizeJsonValue("\udfff");
  const replacement = canonicalizeJsonValue(replacementCharacter);

  assert.equal(replacement, JSON.stringify(replacementCharacter));
  assert.notEqual(loneHigh, replacement);
  assert.notEqual(loneLow, replacement);
  assert.notEqual(loneHigh, loneLow);
  for (let iteration = 0; iteration < 20; iteration += 1) {
    assert.equal(canonicalizeJsonValue("\ud800"), loneHigh);
    assert.equal(canonicalizeJsonValue("\udfff"), loneLow);
    assert.equal(canonicalizeJsonValue(replacementCharacter), replacement);
  }
});

test("never invokes getters or toJSON", () => {
  let getterCalls = 0;
  const withGetter = {};
  Object.defineProperty(withGetter, "secret", {
    enumerable: true,
    get() {
      getterCalls += 1;
      return "not-read";
    },
  });
  assertInvalid(withGetter);
  assert.equal(getterCalls, 0);

  let toJsonCalls = 0;
  const withToJson = {
    value: 1,
    toJSON() {
      toJsonCalls += 1;
      return { value: 2 };
    },
  };
  assertInvalid(withToJson);
  assert.equal(toJsonCalls, 0);
});

test("accepts null-prototype records", () => {
  const record = Object.assign(Object.create(null), { z: 2, a: 1 });
  assert.equal(canonicalizeJsonValue(record), '{"a":1,"z":2}');
});

test("rejects symbols, hidden fields, custom prototypes, and sparse arrays", () => {
  const symbolKey = { visible: true };
  symbolKey[Symbol("hidden")] = true;
  assertInvalid(symbolKey);

  const hiddenField = {};
  Object.defineProperty(hiddenField, "hidden", {
    enumerable: false,
    value: true,
  });
  assertInvalid(hiddenField);

  assertInvalid(Object.assign(Object.create({ inherited: true }), { value: 1 }));
  assertInvalid(new (class CustomRecord { constructor() { this.value = 1; } })());

  const sparse = [];
  sparse.length = 2;
  sparse[1] = "present";
  assertInvalid(sparse);

  const extended = [1];
  extended.extra = 2;
  assertInvalid(extended);
});

test("redacts descriptor trap failures as operational errors", () => {
  const sentinel = new Error("DYNAMIC_SENTINEL_MUST_NOT_LEAK");
  const trapCases = [
    new Proxy(
      {},
      {
        getPrototypeOf() {
          throw sentinel;
        },
      },
    ),
    new Proxy(
      {},
      {
        ownKeys() {
          throw sentinel;
        },
      },
    ),
    new Proxy(
      { value: 1 },
      {
        getOwnPropertyDescriptor() {
          throw sentinel;
        },
      },
    ),
  ];

  for (const value of trapCases) {
    assert.throws(
      () => canonicalizeJsonValue(value),
      (error) => {
        assert.equal(error instanceof CanonicalJsonOperationalError, true);
        assert.equal(error.code, "CANONICAL_JSON_INTERNAL_ERROR");
        assert.equal(error.message, "CANONICAL_JSON_INTERNAL_ERROR");
        assert.equal(error.cause, undefined);
        assert.equal(JSON.stringify(error).includes(sentinel.message), false);
        return true;
      },
    );
  }
});

test("rejects cycles while accepting repeated acyclic references", () => {
  const cycle = {};
  cycle.self = cycle;
  assertInvalid(cycle);

  const shared = { stable: true };
  assert.equal(
    canonicalizeJsonValue({ left: shared, right: shared }),
    '{"left":{"stable":true},"right":{"stable":true}}',
  );
});

test("accepts depth 256 and rejects depth greater than 256", () => {
  let withinLimit = null;
  for (let depth = 0; depth < 256; depth += 1) {
    withinLimit = [withinLimit];
  }
  assert.doesNotThrow(() => canonicalizeJsonValue(withinLimit));

  const beyondLimit = [withinLimit];
  assertInvalid(beyondLimit);
});

test("is deterministic and leaves input data unchanged", () => {
  const input = {
    z: [{ b: 2, a: 1 }],
    a: -0,
  };
  const before = Object.getOwnPropertyDescriptors(input);
  const expected = '{"a":0,"z":[{"a":1,"b":2}]}';
  for (let iteration = 0; iteration < 20; iteration += 1) {
    assert.equal(canonicalizeJsonValue(input), expected);
  }
  assert.deepEqual(Object.getOwnPropertyDescriptors(input), before);
  assert.equal(Object.isFrozen(input), false);
  assert.equal(Object.isFrozen(input.z), false);
});
