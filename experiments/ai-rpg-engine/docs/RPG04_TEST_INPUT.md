# RPG04 neutral prepared input

`tooling/context-test-input.mjs` builds two deterministic test scenarios from the approved RPG04 card fixture. It never edits that fixture. Both scenarios replace the original player biography with a neutral test traveler, set `preferences` to an empty array, omit the original XP-bearing notes, and keep `runtimePermissions` empty.

`buildTestScenario({ world: "gu" | "minecraft" })` returns the cloned card package, neutral player setup, context profile, selected scene, composed approved host template and binding, activation receipt, and canonical bindings. The profile is explicitly rebound to `host.modelmirror-rpg04.approved-composed`; the comparison host is not used.

The Gu scenario selects the approved Gu opening, outer-disciple identity, matching background and item, and all five declared talents. The Minecraft scenario selects the approved Minecraft opening, first-night identity, matching background and item, and its three declared talents. Every selected talent is explicitly `owned: true` and `active: true`.

`createTestContextInput({ scenario, session, turnIndex, modelId, maxTokens })` accepts turn indexes 0 through 2 and emits fixed `action`, `speech`, and `query` inputs. The inputs observe, ask for known facts, and query existing state without buying, refreshing, taking resources, changing state, or changing permissions. IDs are deterministic per world and turn. The helper performs no network or model call; the caller remains responsible for supplying a session whose resource hashes match the returned scenario bindings and for separately authorizing any real dispatch.
