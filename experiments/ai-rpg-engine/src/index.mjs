import Ajv2020 from "ajv/dist/2020.js";

export const FORMAT_VERSION = "0.1.0";
export const FORMATS = Object.freeze({
  cardPackage: "modelmirror.ai-rpg.card-package",
  playerSetup: "modelmirror.ai-rpg.player-setup",
  turnExchange: "modelmirror.ai-rpg.turn-exchange",
  pluginManifest: "modelmirror.ai-rpg.plugin-manifest",
});

export const PLUGIN_CAPABILITIES = Object.freeze([
  "context.enrich",
  "memory.augment",
  "rules.extend",
  "presentation.extend",
  "authoring.transform",
  "evaluation.observe",
]);

export const PLUGIN_PERMISSIONS = Object.freeze([
  "card.read",
  "player.read",
  "turn.read",
  "state.read",
  "state.propose",
  "model.request",
  "memory.read",
  "memory.propose",
  "ui.contribute",
  "content.transform",
  "network.request",
]);

export const PLUGIN_DATA_READ_SCOPES = Object.freeze([
  "card", "playerSetup", "turnInput", "turnProposal", "state", "sessionMetadata",
]);

export const PLUGIN_DATA_PROPOSAL_SCOPES = Object.freeze([
  "context", "state", "informationModule",
]);

const ID_PATTERN = "^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$";
const VERSION_PATTERN = "^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\\+[0-9A-Za-z.-]+)?$";
const SHA256_PATTERN = "^[A-Fa-f0-9]{64}$";
const EXTENSION_NAMESPACE_PATTERN = "^[a-z0-9]+(?:[.-][a-z0-9-]+)*\\.[a-z0-9]+(?:[.-][a-z0-9-]+)*$";
const JSON_SCHEMA_2020_12 = "https://json-schema.org/draft/2020-12/schema";

function strictObject(required, properties, extra = {}) {
  return { type: "object", additionalProperties: false, required, properties, ...extra };
}

function stringSchema(maxLength = 65536, minLength = 1) {
  return { type: "string", minLength, maxLength };
}

const idSchema = { type: "string", pattern: ID_PATTERN };
const idArraySchema = { type: "array", items: idSchema, uniqueItems: true, maxItems: 1024 };
const sourceRefsSchema = { ...idArraySchema, minItems: 1 };
const commonResourceProperties = {
  id: idSchema,
  displayName: stringSchema(256),
  sourceRefs: sourceRefsSchema,
};

function resourceSchema(requiredExtra, extraProperties) {
  return strictObject(
    ["id", "displayName", "sourceRefs", ...requiredExtra],
    { ...commonResourceProperties, ...extraProperties },
  );
}

const stateFieldBase = {
  id: idSchema,
  displayName: stringSchema(256),
  modelMayPropose: { type: "boolean" },
};

const stateFieldSchema = {
  oneOf: [
    strictObject(
      ["id", "displayName", "modelMayPropose", "valueType", "initialValue"],
      { ...stateFieldBase, valueType: { const: "boolean" }, initialValue: { type: "boolean" } },
    ),
    strictObject(
      ["id", "displayName", "modelMayPropose", "valueType", "initialValue"],
      {
        ...stateFieldBase,
        valueType: { const: "integer" },
        initialValue: { type: "integer" },
        minimum: { type: "integer" },
        maximum: { type: "integer" },
      },
    ),
    strictObject(
      ["id", "displayName", "modelMayPropose", "valueType", "initialValue", "maxLength"],
      {
        ...stateFieldBase,
        valueType: { const: "shortText" },
        initialValue: { type: "string", maxLength: 4096 },
        maxLength: { type: "integer", minimum: 1, maximum: 4096 },
      },
    ),
    strictObject(
      ["id", "displayName", "modelMayPropose", "valueType", "initialValue", "choices"],
      {
        ...stateFieldBase,
        valueType: { const: "enum" },
        initialValue: stringSchema(256),
        choices: { type: "array", items: stringSchema(256), minItems: 1, maxItems: 128, uniqueItems: true },
      },
    ),
  ],
};

const pluginRequirementProperties = {
  pluginId: idSchema,
  version: { type: "string", pattern: VERSION_PATTERN },
  capabilities: {
    type: "array",
    items: { enum: PLUGIN_CAPABILITIES },
    uniqueItems: true,
    maxItems: PLUGIN_CAPABILITIES.length,
  },
};

const extensionValueSchema = {
  oneOf: [
    { type: "null" },
    { type: "boolean" },
    { type: "number" },
    { type: "string", maxLength: 1048576 },
    { type: "array", maxItems: 4096, items: { $ref: "#/$defs/extensionValue" } },
    { type: "object", maxProperties: 1024, additionalProperties: { $ref: "#/$defs/extensionValue" } },
  ],
};

const rightsSchema = strictObject(
  ["id", "kind", "name", "reference"],
  {
    id: idSchema,
    kind: { enum: ["license", "authorization"] },
    name: stringSchema(256),
    reference: stringSchema(2048),
    documentSha256: { type: "string", pattern: SHA256_PATTERN },
  },
);

const sourceSchema = strictObject(
  ["id", "kind", "reference", "sha256", "rightsRefs"],
  {
    id: idSchema,
    kind: { enum: ["original", "derived", "authored", "fixture"] },
    reference: stringSchema(2048),
    sha256: { type: "string", pattern: SHA256_PATTERN },
    rightsRefs: { ...idArraySchema, minItems: 1 },
  },
);

const informationFieldSchema = strictObject(
  ["id", "label", "valueType"],
  {
    id: idSchema,
    label: stringSchema(256),
    valueType: { enum: ["text", "number", "boolean", "list"] },
  },
);

export const CARD_PACKAGE_SCHEMA = deepFreeze({
  $schema: JSON_SCHEMA_2020_12,
  $id: "https://modelmirror.local/schemas/ai-rpg/card-package/0.1.0",
  type: "object",
  additionalProperties: false,
  required: [
    "format", "formatVersion", "package", "provenance", "resources", "defaults",
    "stateFields", "requiredPlugins", "recommendedPlugins",
  ],
  properties: {
    format: { const: FORMATS.cardPackage },
    formatVersion: { const: FORMAT_VERSION },
    package: strictObject(
      ["id", "version", "displayName", "locale"],
      {
        id: idSchema,
        version: { type: "string", pattern: VERSION_PATTERN },
        displayName: stringSchema(256),
        description: stringSchema(8192),
        locale: { type: "string", pattern: "^[a-z]{2,3}(?:-[A-Z][a-z]{3})?(?:-[A-Z]{2}|-[0-9]{3})?$" },
      },
    ),
    provenance: strictObject(
      ["rights", "sources"],
      {
        rights: { type: "array", items: rightsSchema, minItems: 1, maxItems: 1024 },
        sources: { type: "array", items: sourceSchema, minItems: 1, maxItems: 4096 },
      },
    ),
    resources: strictObject(
      [
        "worlds", "identities", "talents", "items", "backgrounds", "styles",
        "worldbookEntries", "openings", "informationModules", "commands",
      ],
      {
        worlds: {
          type: "array", maxItems: 4096,
          items: resourceSchema(["description"], { description: stringSchema(1048576) }),
        },
        identities: {
          type: "array", maxItems: 4096,
          items: resourceSchema(["description", "rankLabel", "worldRefs"], {
            description: stringSchema(1048576),
            rankLabel: stringSchema(128),
            worldRefs: { ...idArraySchema, minItems: 1 },
          }),
        },
        talents: {
          type: "array", maxItems: 4096,
          items: resourceSchema(["description", "tierLabel", "worldRefs"], {
            description: stringSchema(1048576),
            tierLabel: stringSchema(128),
            worldRefs: idArraySchema,
          }),
        },
        items: {
          type: "array", maxItems: 4096,
          items: resourceSchema(["description"], { description: stringSchema(1048576) }),
        },
        backgrounds: {
          type: "array", maxItems: 4096,
          items: resourceSchema(["description"], { description: stringSchema(1048576) }),
        },
        styles: {
          type: "array", maxItems: 4096,
          items: resourceSchema(["instruction"], { instruction: stringSchema(1048576) }),
        },
        worldbookEntries: {
          type: "array", maxItems: 16384,
          items: resourceSchema(["content", "tags", "visibility", "worldRefs"], {
            content: stringSchema(1048576),
            tags: { type: "array", items: stringSchema(128), uniqueItems: true, maxItems: 128 },
            visibility: { enum: ["player", "host", "shared"] },
            worldRefs: idArraySchema,
          }),
        },
        openings: {
          type: "array", maxItems: 4096,
          items: resourceSchema(
            [
              "content", "worldRef", "identityRefs", "talentRefs", "itemRefs", "backgroundRefs",
              "styleRefs", "worldbookRefs", "informationModuleRefs",
            ],
            {
              content: stringSchema(1048576),
              worldRef: idSchema,
              identityRefs: idArraySchema,
              talentRefs: idArraySchema,
              itemRefs: idArraySchema,
              backgroundRefs: idArraySchema,
              styleRefs: idArraySchema,
              worldbookRefs: idArraySchema,
              informationModuleRefs: idArraySchema,
            },
          ),
        },
        informationModules: {
          type: "array", maxItems: 4096,
          items: resourceSchema(["presentation", "fields"], {
            presentation: { enum: ["text", "keyValue", "list", "meter"] },
            description: stringSchema(8192),
            fields: { type: "array", items: informationFieldSchema, minItems: 1, maxItems: 128 },
          }),
        },
        commands: {
          type: "array", maxItems: 1024,
          items: resourceSchema(["description"], { description: stringSchema(8192) }),
        },
      },
    ),
    defaults: strictObject(
      ["worldRef", "openingRef"],
      { worldRef: idSchema, openingRef: idSchema },
    ),
    stateFields: { type: "array", items: stateFieldSchema, maxItems: 1024 },
    requiredPlugins: {
      type: "array", maxItems: 256,
      items: strictObject(["pluginId", "version", "capabilities"], pluginRequirementProperties),
    },
    recommendedPlugins: {
      type: "array", maxItems: 256,
      items: strictObject(
        ["pluginId", "version", "capabilities", "fallback"],
        { ...pluginRequirementProperties, fallback: { enum: ["core", "omit", "readOnly"] } },
      ),
    },
    extensions: {
      type: "object",
      maxProperties: 256,
      propertyNames: { pattern: EXTENSION_NAMESPACE_PATTERN },
      additionalProperties: { $ref: "#/$defs/extensionValue" },
    },
  },
  $defs: { extensionValue: extensionValueSchema },
});

const customResourceSchema = strictObject(
  ["id", "kind", "displayName", "description"],
  {
    id: idSchema,
    kind: { enum: ["world", "identity", "talent", "item", "background"] },
    displayName: stringSchema(256),
    description: stringSchema(1048576),
    tierLabel: stringSchema(128),
    rankLabel: stringSchema(128),
    tags: { type: "array", items: stringSchema(128), uniqueItems: true, maxItems: 128 },
  },
);

const resourceChoiceSchema = {
  oneOf: [
    strictObject(
      ["source", "resourceRef"],
      { source: { const: "package" }, resourceRef: idSchema },
    ),
    strictObject(
      ["source", "resource"],
      { source: { const: "custom" }, resource: customResourceSchema },
    ),
  ],
};

export const PLAYER_SETUP_SCHEMA = deepFreeze({
  $schema: JSON_SCHEMA_2020_12,
  $id: "https://modelmirror.local/schemas/ai-rpg/player-setup/0.1.0",
  type: "object",
  additionalProperties: false,
  required: [
    "format", "formatVersion", "setupId", "cardPackageRef", "character", "opening",
    "world", "currentIdentity", "inherentBackgrounds", "possessions", "talents",
    "characterPower", "runtimePermissions",
  ],
  properties: {
    format: { const: FORMATS.playerSetup },
    formatVersion: { const: FORMAT_VERSION },
    setupId: idSchema,
    cardPackageRef: strictObject(
      ["id", "version"],
      { id: idSchema, version: { type: "string", pattern: VERSION_PATTERN } },
    ),
    character: strictObject(
      ["name", "appearance", "personality", "preferences"],
      {
        name: stringSchema(256),
        gender: stringSchema(128),
        age: { type: "integer", minimum: 0, maximum: 1000 },
        appearance: stringSchema(65536),
        personality: stringSchema(65536),
        preferences: { type: "array", items: stringSchema(512), uniqueItems: true, maxItems: 128 },
        notes: stringSchema(65536),
      },
    ),
    opening: strictObject(
      ["mode", "openingRef"],
      { mode: stringSchema(256), openingRef: idSchema },
    ),
    world: resourceChoiceSchema,
    currentIdentity: resourceChoiceSchema,
    inherentBackgrounds: { type: "array", items: resourceChoiceSchema, maxItems: 64 },
    possessions: {
      type: "array",
      maxItems: 1024,
      items: strictObject(
        ["resource", "quantity"],
        { resource: resourceChoiceSchema, quantity: { type: "integer", minimum: 1, maximum: 1000000000 } },
      ),
    },
    talents: {
      type: "array",
      maxItems: 1024,
      items: strictObject(
        ["resource", "owned", "active"],
        { resource: resourceChoiceSchema, owned: { type: "boolean" }, active: { type: "boolean" } },
      ),
    },
    characterPower: {
      oneOf: [
        strictObject(["status"], { status: { const: "unspecified" } }),
        strictObject(
          ["status", "rankLabel"],
          { status: { const: "declared" }, rankLabel: stringSchema(128), description: stringSchema(8192) },
        ),
      ],
    },
    runtimePermissions: { type: "array", maxItems: 0 },
  },
});

const turnInputSchema = {
  oneOf: [
    ...["action", "speech", "query"].map((kind) => strictObject(
      ["kind", "text"],
      { kind: { const: kind }, text: stringSchema(65536) },
    )),
    strictObject(
      ["kind", "commandRef", "text"],
      { kind: { const: "command" }, commandRef: idSchema, text: stringSchema(65536) },
    ),
  ],
};

const suggestedActionSchema = {
  oneOf: [
    ...["action", "speech", "query"].map((inputKind) => strictObject(
      ["id", "label", "inputKind", "text"],
      { id: idSchema, label: stringSchema(256), inputKind: { const: inputKind }, text: stringSchema(65536) },
    )),
    strictObject(
      ["id", "label", "inputKind", "commandRef", "text"],
      {
        id: idSchema,
        label: stringSchema(256),
        inputKind: { const: "command" },
        commandRef: idSchema,
        text: stringSchema(65536),
      },
    ),
  ],
};

const informationValueSchema = {
  oneOf: [
    { type: "string", maxLength: 65536 },
    { type: "number" },
    { type: "boolean" },
    { type: "array", items: { type: "string", maxLength: 8192 }, maxItems: 1024 },
  ],
};

export const TURN_EXCHANGE_SCHEMA = deepFreeze({
  $schema: JSON_SCHEMA_2020_12,
  $id: "https://modelmirror.local/schemas/ai-rpg/turn-exchange/0.1.0",
  type: "object",
  additionalProperties: false,
  required: ["format", "formatVersion", "exchangeId", "cardPackageRef", "input", "proposal"],
  properties: {
    format: { const: FORMATS.turnExchange },
    formatVersion: { const: FORMAT_VERSION },
    exchangeId: idSchema,
    cardPackageRef: strictObject(
      ["id", "version"],
      { id: idSchema, version: { type: "string", pattern: VERSION_PATTERN } },
    ),
    input: turnInputSchema,
    proposal: strictObject(
      ["narrative", "suggestedActions", "informationModules", "stateProposals", "uncertainties"],
      {
        narrative: stringSchema(1048576),
        suggestedActions: { type: "array", items: suggestedActionSchema, maxItems: 128 },
        informationModules: {
          type: "array",
          maxItems: 128,
          items: strictObject(
            ["moduleRef", "values"],
            {
              moduleRef: idSchema,
              values: {
                type: "array",
                maxItems: 128,
                items: strictObject(
                  ["fieldRef", "value"],
                  { fieldRef: idSchema, value: informationValueSchema },
                ),
              },
            },
          ),
        },
        stateProposals: {
          type: "array",
          maxItems: 1024,
          items: strictObject(
            ["fieldRef", "proposedValue"],
            {
              fieldRef: idSchema,
              proposedValue: { oneOf: [{ type: "boolean" }, { type: "integer" }, { type: "string", maxLength: 4096 }] },
              rationale: stringSchema(8192),
            },
          ),
        },
        uncertainties: {
          type: "array",
          maxItems: 128,
          items: strictObject(
            ["code", "description", "relatedResourceRefs"],
            { code: idSchema, description: stringSchema(8192), relatedResourceRefs: idArraySchema },
          ),
        },
      },
    ),
  },
});

const pluginSettingSchema = {
  oneOf: [
    strictObject(
      ["key", "label", "description", "valueType", "required"],
      { key: idSchema, label: stringSchema(256), description: stringSchema(8192), valueType: { const: "boolean" }, required: { type: "boolean" } },
    ),
    strictObject(
      ["key", "label", "description", "valueType", "required"],
      {
        key: idSchema,
        label: stringSchema(256),
        description: stringSchema(8192),
        valueType: { const: "integer" },
        required: { type: "boolean" },
        minimum: { type: "integer" },
        maximum: { type: "integer" },
      },
    ),
    strictObject(
      ["key", "label", "description", "valueType", "required", "maxLength"],
      {
        key: idSchema,
        label: stringSchema(256),
        description: stringSchema(8192),
        valueType: { const: "shortText" },
        required: { type: "boolean" },
        maxLength: { type: "integer", minimum: 1, maximum: 4096 },
      },
    ),
    strictObject(
      ["key", "label", "description", "valueType", "required", "choices"],
      {
        key: idSchema,
        label: stringSchema(256),
        description: stringSchema(8192),
        valueType: { const: "enum" },
        required: { type: "boolean" },
        choices: { type: "array", items: stringSchema(256), minItems: 1, maxItems: 128, uniqueItems: true },
      },
    ),
  ],
};

const pluginDependencySchema = strictObject(
  ["pluginId", "version", "capabilities"],
  {
    pluginId: idSchema,
    version: { type: "string", pattern: VERSION_PATTERN },
    capabilities: {
      type: "array",
      items: { enum: PLUGIN_CAPABILITIES },
      uniqueItems: true,
      maxItems: PLUGIN_CAPABILITIES.length,
    },
  },
);

export const PLUGIN_MANIFEST_SCHEMA = deepFreeze({
  $schema: JSON_SCHEMA_2020_12,
  $id: "https://modelmirror.local/schemas/ai-rpg/plugin-manifest/0.1.0",
  type: "object",
  additionalProperties: false,
  required: [
    "format", "formatVersion", "plugin", "compatibleHostContractVersions", "capabilities",
    "permissions", "settings", "dependencies", "dataAccess", "network", "lifecycle", "provenance",
  ],
  properties: {
    format: { const: FORMATS.pluginManifest },
    formatVersion: { const: FORMAT_VERSION },
    plugin: strictObject(
      ["id", "version", "displayName", "description"],
      {
        id: idSchema,
        version: { type: "string", pattern: VERSION_PATTERN },
        displayName: stringSchema(256),
        description: stringSchema(8192),
      },
    ),
    compatibleHostContractVersions: {
      type: "array", items: { const: FORMAT_VERSION }, minItems: 1, maxItems: 1, uniqueItems: true,
    },
    capabilities: {
      type: "array", items: { enum: PLUGIN_CAPABILITIES }, uniqueItems: true, maxItems: PLUGIN_CAPABILITIES.length,
    },
    permissions: {
      type: "array", items: { enum: PLUGIN_PERMISSIONS }, uniqueItems: true, maxItems: PLUGIN_PERMISSIONS.length,
    },
    settings: { type: "array", items: pluginSettingSchema, maxItems: 256 },
    dependencies: { type: "array", items: pluginDependencySchema, maxItems: 256 },
    dataAccess: strictObject(
      ["read", "propose"],
      {
        read: { type: "array", items: { enum: PLUGIN_DATA_READ_SCOPES }, uniqueItems: true, maxItems: PLUGIN_DATA_READ_SCOPES.length },
        propose: { type: "array", items: { enum: PLUGIN_DATA_PROPOSAL_SCOPES }, uniqueItems: true, maxItems: PLUGIN_DATA_PROPOSAL_SCOPES.length },
      },
    ),
    network: {
      oneOf: [
        strictObject(["mode"], { mode: { const: "none" } }),
        strictObject(
          ["mode", "allowedHosts", "purposes"],
          {
            mode: { const: "modelmirror-mediated" },
            allowedHosts: {
              type: "array",
              items: { type: "string", minLength: 1, maxLength: 253, pattern: "^[A-Za-z0-9.-]+$" },
              minItems: 1,
              maxItems: 128,
              uniqueItems: true,
            },
            purposes: { type: "array", items: stringSchema(512), minItems: 1, maxItems: 64, uniqueItems: true },
          },
        ),
      ],
    },
    lifecycle: strictObject(
      ["activation", "deactivation", "failurePolicy", "uninstallData"],
      {
        activation: { const: "explicit" },
        deactivation: { const: "supported" },
        failurePolicy: { const: "isolated" },
        uninstallData: { enum: ["retain", "deleteOnRequest"] },
      },
    ),
    provenance: strictObject(
      ["sourceReference", "sourceSha256", "licenseName", "licenseReference", "artifactSha256"],
      {
        sourceReference: stringSchema(2048),
        sourceSha256: { type: "string", pattern: SHA256_PATTERN },
        licenseName: stringSchema(256),
        licenseReference: stringSchema(2048),
        artifactSha256: { type: "string", pattern: SHA256_PATTERN },
      },
    ),
  },
});

export const SCHEMAS = deepFreeze({
  cardPackage: CARD_PACKAGE_SCHEMA,
  playerSetup: PLAYER_SETUP_SCHEMA,
  turnExchange: TURN_EXCHANGE_SCHEMA,
  pluginManifest: PLUGIN_MANIFEST_SCHEMA,
});

const ajv = new Ajv2020({
  strict: true,
  allErrors: true,
  validateSchema: true,
  validateFormats: false,
  messages: false,
  verbose: false,
  coerceTypes: false,
  useDefaults: false,
  removeAdditional: false,
  ownProperties: true,
  allowUnionTypes: false,
});

const validateCardPackageStructure = ajv.compile(CARD_PACKAGE_SCHEMA);
const validatePlayerSetupStructure = ajv.compile(PLAYER_SETUP_SCHEMA);
const validateTurnExchangeStructure = ajv.compile(TURN_EXCHANGE_SCHEMA);
const validatePluginManifestStructure = ajv.compile(PLUGIN_MANIFEST_SCHEMA);

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) deepFreeze(child);
  return Object.freeze(value);
}

function escapePointerToken(token) {
  return String(token).replaceAll("~", "~0").replaceAll("/", "~1");
}

function at(base, token) {
  return `${base}/${escapePointerToken(token)}`;
}

function diagnostic(phase, severity, code, path = "", relatedPath) {
  const item = { phase, severity, code, path };
  if (relatedPath !== undefined) item.relatedPath = relatedPath;
  return Object.freeze(item);
}

const PHASE_ORDER = new Map([["schema", 0], ["reference", 1], ["policy", 2], ["readiness", 3]]);
const SEVERITY_ORDER = new Map([["error", 0], ["warning", 1]]);

function normalizeDiagnostics(items) {
  const unique = new Map();
  for (const item of items) {
    const key = [item.phase, item.severity, item.code, item.path, item.relatedPath ?? ""].join("\u0000");
    unique.set(key, item);
  }
  return Object.freeze([...unique.values()].sort((left, right) => {
    const fields = [
      (PHASE_ORDER.get(left.phase) ?? 99) - (PHASE_ORDER.get(right.phase) ?? 99),
      (SEVERITY_ORDER.get(left.severity) ?? 99) - (SEVERITY_ORDER.get(right.severity) ?? 99),
      left.path.localeCompare(right.path),
      left.code.localeCompare(right.code),
      (left.relatedPath ?? "").localeCompare(right.relatedPath ?? ""),
    ];
    return fields.find((value) => value !== 0) ?? 0;
  }));
}

const SCHEMA_CODE_SUFFIX = Object.freeze({
  required: "REQUIRED",
  additionalProperties: "UNKNOWN_PROPERTY",
  type: "TYPE",
  const: "VERSION_OR_FORMAT",
  enum: "ENUM",
  pattern: "PATTERN",
  minLength: "STRING_BOUNDS",
  maxLength: "STRING_BOUNDS",
  minItems: "ARRAY_BOUNDS",
  maxItems: "ARRAY_BOUNDS",
  uniqueItems: "DUPLICATE_ITEM",
  minimum: "NUMBER_BOUNDS",
  maximum: "NUMBER_BOUNDS",
  oneOf: "VARIANT",
  propertyNames: "PROPERTY_NAME",
});

function schemaDiagnostics(validator, prefix, root = "") {
  return (validator.errors ?? []).map((error) => {
    let pointer = `${root}${error.instancePath}`;
    if (error.keyword === "required") pointer = at(pointer, error.params.missingProperty);
    if (error.keyword === "additionalProperties" && /^[A-Za-z][A-Za-z0-9_-]{0,63}$/u.test(error.params.additionalProperty)) {
      pointer = at(pointer, error.params.additionalProperty);
    }
    const suffix = SCHEMA_CODE_SUFFIX[error.keyword] ?? "INVALID";
    return diagnostic("schema", "error", `${prefix}_SCHEMA_${suffix}`, pointer);
  });
}

function validationReport(items) {
  const diagnostics = normalizeDiagnostics(items);
  return Object.freeze({
    valid: !diagnostics.some((item) => item.severity === "error"),
    diagnostics,
  });
}

function duplicateDiagnostics(items, basePath, code, sharedIndex) {
  const diagnostics = [];
  const firstPaths = sharedIndex ?? new Map();
  items.forEach((item, index) => {
    const currentPath = `${basePath}/${index}/id`;
    const firstPath = firstPaths.get(item.id);
    if (firstPath !== undefined) diagnostics.push(diagnostic("reference", "error", code, currentPath, firstPath));
    else firstPaths.set(item.id, currentPath);
  });
  return diagnostics;
}

function referenceDiagnostics(refs, index, basePath, code) {
  const diagnostics = [];
  refs.forEach((reference, offset) => {
    if (!index.has(reference)) diagnostics.push(diagnostic("reference", "error", code, `${basePath}/${offset}`));
  });
  return diagnostics;
}

const EXECUTABLE_EXTENSION_KEYS = new Set([
  "script", "rawhtml", "javascript", "executable", "entrypoint", "shell", "argv", "eval",
  "toolcall", "functioncall", "mcpserver", "network", "fetch", "webhook", "autoinstall",
  "autoenable", "autoupdate", "autoupgrade", "installer", "permission", "permissions",
]);

const EXECUTABLE_EXTENSION_TOKENS = new Set([
  "script", "javascript", "executable", "entrypoint", "shell", "argv", "eval", "network",
  "fetch", "webhook", "installer", "permission", "permissions",
]);

function extensionKeyTokens(key) {
  return key
    .replaceAll(/([a-z0-9])([A-Z])/gu, "$1 $2")
    .replaceAll(/([A-Z]+)([A-Z][a-z])/gu, "$1 $2")
    .toLowerCase()
    .split(/[^a-z0-9]+/gu)
    .filter(Boolean);
}

function isExecutableExtensionKey(key) {
  const normalized = key.toLowerCase().replaceAll(/[^a-z0-9]/gu, "");
  if (EXECUTABLE_EXTENSION_KEYS.has(normalized) ||
      [...EXECUTABLE_EXTENSION_KEYS].some((candidate) => normalized.startsWith(candidate))) return true;
  const tokens = extensionKeyTokens(key);
  if (tokens.some((token) => EXECUTABLE_EXTENSION_TOKENS.has(token))) return true;
  const pairs = new Set(tokens.slice(0, -1).map((token, index) => token + tokens[index + 1]));
  return ["rawhtml", "toolcall", "functioncall", "mcpserver", "autoinstall", "autoenable", "autoupdate", "autoupgrade"]
    .some((pair) => pairs.has(pair));
}

function extensionPolicyDiagnostics(extensions) {
  const diagnostics = [];
  if (!extensions || typeof extensions !== "object" || Array.isArray(extensions)) return diagnostics;
  if (![Object.prototype, null].includes(Object.getPrototypeOf(extensions))) {
    diagnostics.push(diagnostic("policy", "error", "CARD_PACKAGE_EXTENSION_NON_JSON", "/extensions"));
    return diagnostics;
  }
  const maximumDepth = 16;
  const maximumNodes = 16384;
  const ancestors = new WeakSet();
  const stack = [];
  let nodes = 0;
  let limitReported = false;
  for (const [namespace, value] of Object.entries(extensions)) {
    stack.push({ kind: "value", value, namespacePath: `/extensions/${escapePointerToken(namespace)}`, depth: 0 });
  }
  while (stack.length > 0) {
    const frame = stack.pop();
    if (frame.kind === "leave") {
      ancestors.delete(frame.value);
      continue;
    }
    const { value, namespacePath, depth } = frame;
    nodes += 1;
    if (depth > maximumDepth || nodes > maximumNodes) {
      if (!limitReported) diagnostics.push(diagnostic("policy", "error", "CARD_PACKAGE_EXTENSION_LIMIT", namespacePath));
      limitReported = true;
      continue;
    }
    if (value === null || typeof value === "string" || typeof value === "boolean") continue;
    if (typeof value === "number") {
      if (!Number.isFinite(value)) diagnostics.push(diagnostic("policy", "error", "CARD_PACKAGE_EXTENSION_NON_JSON", namespacePath));
      continue;
    }
    if (typeof value !== "object") {
      diagnostics.push(diagnostic("policy", "error", "CARD_PACKAGE_EXTENSION_NON_JSON", namespacePath));
      continue;
    }
    if (ancestors.has(value)) {
      diagnostics.push(diagnostic("policy", "error", "CARD_PACKAGE_EXTENSION_NON_JSON", namespacePath));
      continue;
    }
    if (!Array.isArray(value) && ![Object.prototype, null].includes(Object.getPrototypeOf(value))) {
      diagnostics.push(diagnostic("policy", "error", "CARD_PACKAGE_EXTENSION_NON_JSON", namespacePath));
      continue;
    }
    ancestors.add(value);
    stack.push({ kind: "leave", value });
    if (Array.isArray(value)) {
      if (value.length > maximumNodes - nodes) {
        if (!limitReported) diagnostics.push(diagnostic("policy", "error", "CARD_PACKAGE_EXTENSION_LIMIT", namespacePath));
        limitReported = true;
        continue;
      }
      for (let index = value.length - 1; index >= 0; index -= 1) {
        stack.push({ kind: "value", value: value[index], namespacePath, depth: depth + 1 });
      }
      continue;
    }
    const entries = Object.entries(value);
    if (entries.length > maximumNodes - nodes) {
      if (!limitReported) diagnostics.push(diagnostic("policy", "error", "CARD_PACKAGE_EXTENSION_LIMIT", namespacePath));
      limitReported = true;
      continue;
    }
    for (let index = entries.length - 1; index >= 0; index -= 1) {
      const [key, child] = entries[index];
      if (isExecutableExtensionKey(key)) {
        diagnostics.push(diagnostic("policy", "error", "CARD_PACKAGE_EXECUTABLE_FIELD_FORBIDDEN", namespacePath));
      } else {
        stack.push({ kind: "value", value: child, namespacePath, depth: depth + 1 });
      }
    }
  }
  return diagnostics;
}

function cardSemanticDiagnostics(cardPackage) {
  const diagnostics = [];
  const stableIds = new Map();
  function claimStableId(id, path) {
    const firstPath = stableIds.get(id);
    if (firstPath !== undefined) {
      diagnostics.push(diagnostic("reference", "error", "CARD_PACKAGE_STABLE_ID_DUPLICATE", path, firstPath));
    } else {
      stableIds.set(id, path);
    }
  }
  claimStableId(cardPackage.package.id, "/package/id");
  const rights = new Map();
  cardPackage.provenance.rights.forEach((right, index) => {
    const idPath = `/provenance/rights/${index}/id`;
    claimStableId(right.id, idPath);
    if (!rights.has(right.id)) rights.set(right.id, idPath);
  });
  const sources = new Map();
  cardPackage.provenance.sources.forEach((source, index) => {
    const idPath = `/provenance/sources/${index}/id`;
    claimStableId(source.id, idPath);
    if (!sources.has(source.id)) sources.set(source.id, idPath);
  });
  cardPackage.provenance.sources.forEach((source, index) => {
    diagnostics.push(...referenceDiagnostics(source.rightsRefs, rights, `/provenance/sources/${index}/rightsRefs`, "CARD_PACKAGE_RIGHT_REF_MISSING"));
  });

  const indexes = {};
  for (const [kind, resources] of Object.entries(cardPackage.resources)) {
    const index = new Map();
    indexes[kind] = index;
    resources.forEach((resource, offset) => {
      const resourcePath = `/resources/${kind}/${offset}`;
      claimStableId(resource.id, `${resourcePath}/id`);
      index.set(resource.id, resourcePath);
      diagnostics.push(...referenceDiagnostics(resource.sourceRefs, sources, `${resourcePath}/sourceRefs`, "CARD_PACKAGE_SOURCE_REF_MISSING"));
      if (kind === "informationModules") {
        resource.fields.forEach((field, fieldIndex) => claimStableId(field.id, `${resourcePath}/fields/${fieldIndex}/id`));
      }
    });
  }

  cardPackage.resources.identities.forEach((resource, index) => {
    diagnostics.push(...referenceDiagnostics(resource.worldRefs, indexes.worlds, `/resources/identities/${index}/worldRefs`, "CARD_PACKAGE_WORLD_REF_MISSING"));
  });
  cardPackage.resources.talents.forEach((resource, index) => {
    diagnostics.push(...referenceDiagnostics(resource.worldRefs, indexes.worlds, `/resources/talents/${index}/worldRefs`, "CARD_PACKAGE_WORLD_REF_MISSING"));
  });
  cardPackage.resources.worldbookEntries.forEach((resource, index) => {
    diagnostics.push(...referenceDiagnostics(resource.worldRefs, indexes.worlds, `/resources/worldbookEntries/${index}/worldRefs`, "CARD_PACKAGE_WORLD_REF_MISSING"));
  });
  cardPackage.resources.openings.forEach((opening, index) => {
    const base = `/resources/openings/${index}`;
    if (!indexes.worlds.has(opening.worldRef)) diagnostics.push(diagnostic("reference", "error", "CARD_PACKAGE_WORLD_REF_MISSING", `${base}/worldRef`));
    for (const [field, kind, code] of [
      ["identityRefs", "identities", "CARD_PACKAGE_IDENTITY_REF_MISSING"],
      ["talentRefs", "talents", "CARD_PACKAGE_TALENT_REF_MISSING"],
      ["itemRefs", "items", "CARD_PACKAGE_ITEM_REF_MISSING"],
      ["backgroundRefs", "backgrounds", "CARD_PACKAGE_BACKGROUND_REF_MISSING"],
      ["styleRefs", "styles", "CARD_PACKAGE_STYLE_REF_MISSING"],
      ["worldbookRefs", "worldbookEntries", "CARD_PACKAGE_WORLDBOOK_REF_MISSING"],
      ["informationModuleRefs", "informationModules", "CARD_PACKAGE_INFORMATION_MODULE_REF_MISSING"],
    ]) diagnostics.push(...referenceDiagnostics(opening[field], indexes[kind], `${base}/${field}`, code));
  });
  if (!indexes.worlds.has(cardPackage.defaults.worldRef)) diagnostics.push(diagnostic("reference", "error", "CARD_PACKAGE_WORLD_REF_MISSING", "/defaults/worldRef"));
  if (!indexes.openings.has(cardPackage.defaults.openingRef)) diagnostics.push(diagnostic("reference", "error", "CARD_PACKAGE_OPENING_REF_MISSING", "/defaults/openingRef"));
  const defaultOpeningPath = indexes.openings.get(cardPackage.defaults.openingRef);
  const defaultOpening = defaultOpeningPath === undefined
    ? null
    : cardPackage.resources.openings.find((opening) => opening.id === cardPackage.defaults.openingRef);
  if (defaultOpening && defaultOpening.worldRef !== cardPackage.defaults.worldRef) {
    diagnostics.push(diagnostic("reference", "error", "CARD_PACKAGE_DEFAULT_OPENING_WORLD_MISMATCH", "/defaults/openingRef", "/defaults/worldRef"));
  }

  cardPackage.stateFields.forEach((field, index) => {
    const base = `/stateFields/${index}`;
    claimStableId(field.id, `${base}/id`);
    if (field.valueType === "integer") {
      if (field.minimum !== undefined && field.maximum !== undefined && field.minimum > field.maximum) {
        diagnostics.push(diagnostic("policy", "error", "CARD_PACKAGE_STATE_INTEGER_RANGE_INVALID", `${base}/minimum`, `${base}/maximum`));
      }
      if ((field.minimum !== undefined && field.initialValue < field.minimum) ||
          (field.maximum !== undefined && field.initialValue > field.maximum)) {
        diagnostics.push(diagnostic("policy", "error", "CARD_PACKAGE_STATE_INITIAL_VALUE_INVALID", `${base}/initialValue`));
      }
    }
    if (field.valueType === "shortText" && field.initialValue.length > field.maxLength) {
      diagnostics.push(diagnostic("policy", "error", "CARD_PACKAGE_STATE_INITIAL_VALUE_INVALID", `${base}/initialValue`, `${base}/maxLength`));
    }
    if (field.valueType === "enum" && !field.choices.includes(field.initialValue)) {
      diagnostics.push(diagnostic("policy", "error", "CARD_PACKAGE_STATE_INITIAL_VALUE_INVALID", `${base}/initialValue`, `${base}/choices`));
    }
  });

  const pluginIds = new Map();
  for (const [field, requirements] of [["requiredPlugins", cardPackage.requiredPlugins], ["recommendedPlugins", cardPackage.recommendedPlugins]]) {
    requirements.forEach((requirement, index) => {
      const currentPath = `/${field}/${index}/pluginId`;
      const firstPath = pluginIds.get(requirement.pluginId);
      if (firstPath !== undefined) diagnostics.push(diagnostic("reference", "error", "CARD_PACKAGE_PLUGIN_REQUIREMENT_DUPLICATE", currentPath, firstPath));
      else pluginIds.set(requirement.pluginId, currentPath);
    });
  }
  return diagnostics;
}

export function validateCardPackage(value) {
  try {
    if (value && typeof value === "object" && Object.hasOwn(value, "extensions")) {
      const extensionDiagnostics = extensionPolicyDiagnostics(value.extensions);
      if (extensionDiagnostics.length > 0) return validationReport(extensionDiagnostics);
    }
  } catch {
    return validationReport([diagnostic("policy", "error", "CARD_PACKAGE_EXTENSION_NON_JSON", "/extensions")]);
  }
  if (!validateCardPackageStructure(value)) return validationReport(schemaDiagnostics(validateCardPackageStructure, "CARD_PACKAGE"));
  return validationReport(cardSemanticDiagnostics(value));
}

function packageResourceIndexes(cardPackage) {
  const indexes = {};
  const all = new Map();
  for (const [collection, resources] of Object.entries(cardPackage.resources)) {
    const index = new Map();
    indexes[collection] = index;
    resources.forEach((resource, offset) => {
      const value = { resource, path: `/resources/${collection}/${offset}` };
      index.set(resource.id, value);
      all.set(resource.id, { ...value, collection });
    });
  }
  return { indexes, all };
}

function playerSetupSemanticDiagnostics(playerSetup, cardPackage) {
  const diagnostics = [];
  if (playerSetup.cardPackageRef.id !== cardPackage.package.id ||
      playerSetup.cardPackageRef.version !== cardPackage.package.version) {
    diagnostics.push(diagnostic("reference", "error", "PLAYER_SETUP_CARD_PACKAGE_REF_MISMATCH", "/cardPackageRef"));
  }

  const { indexes, all } = packageResourceIndexes(cardPackage);
  const customIds = new Map();
  function checkChoice(choice, expectedCollection, expectedKind, basePath, missingCode) {
    if (choice.source === "package") {
      if (indexes[expectedCollection].has(choice.resourceRef)) return indexes[expectedCollection].get(choice.resourceRef).resource;
      if (all.has(choice.resourceRef)) {
        diagnostics.push(diagnostic("reference", "error", "PLAYER_SETUP_RESOURCE_KIND_MISMATCH", `${basePath}/resourceRef`, all.get(choice.resourceRef).path));
      } else {
        diagnostics.push(diagnostic("reference", "error", missingCode, `${basePath}/resourceRef`));
      }
      return null;
    }
    if (choice.resource.kind !== expectedKind) {
      diagnostics.push(diagnostic("reference", "error", "PLAYER_SETUP_CUSTOM_RESOURCE_KIND_MISMATCH", `${basePath}/resource/kind`));
    }
    const idPath = `${basePath}/resource/id`;
    if (all.has(choice.resource.id)) {
      diagnostics.push(diagnostic("reference", "error", "PLAYER_SETUP_CUSTOM_RESOURCE_ID_COLLISION", idPath, all.get(choice.resource.id).path));
    } else if (customIds.has(choice.resource.id)) {
      diagnostics.push(diagnostic("reference", "error", "PLAYER_SETUP_CUSTOM_RESOURCE_ID_COLLISION", idPath, customIds.get(choice.resource.id)));
    } else {
      customIds.set(choice.resource.id, idPath);
    }
    return choice.resource;
  }

  const world = checkChoice(playerSetup.world, "worlds", "world", "/world", "PLAYER_SETUP_WORLD_REF_MISSING");
  const identity = checkChoice(playerSetup.currentIdentity, "identities", "identity", "/currentIdentity", "PLAYER_SETUP_IDENTITY_REF_MISSING");
  playerSetup.inherentBackgrounds.forEach((choice, index) => {
    checkChoice(choice, "backgrounds", "background", `/inherentBackgrounds/${index}`, "PLAYER_SETUP_BACKGROUND_REF_MISSING");
  });
  playerSetup.possessions.forEach((entry, index) => {
    checkChoice(entry.resource, "items", "item", `/possessions/${index}/resource`, "PLAYER_SETUP_ITEM_REF_MISSING");
  });
  const talents = playerSetup.talents.map((entry, index) => {
    if (entry.active && !entry.owned) {
      diagnostics.push(diagnostic("policy", "error", "PLAYER_SETUP_UNOWNED_TALENT_ACTIVE", `/talents/${index}/active`, `/talents/${index}/owned`));
    }
    return checkChoice(entry.resource, "talents", "talent", `/talents/${index}/resource`, "PLAYER_SETUP_TALENT_REF_MISSING");
  });

  const opening = indexes.openings.get(playerSetup.opening.openingRef)?.resource;
  if (!opening) diagnostics.push(diagnostic("reference", "error", "PLAYER_SETUP_OPENING_REF_MISSING", "/opening/openingRef"));
  const selectedWorldId = playerSetup.world.source === "package" ? playerSetup.world.resourceRef : null;
  if (selectedWorldId && opening && opening.worldRef !== selectedWorldId) {
    diagnostics.push(diagnostic("reference", "error", "PLAYER_SETUP_OPENING_WORLD_MISMATCH", "/opening/openingRef", "/world/resourceRef"));
  }
  if (selectedWorldId && playerSetup.currentIdentity.source === "package" && identity && !identity.worldRefs.includes(selectedWorldId)) {
    diagnostics.push(diagnostic("reference", "error", "PLAYER_SETUP_IDENTITY_WORLD_MISMATCH", "/currentIdentity/resourceRef", "/world/resourceRef"));
  }
  if (selectedWorldId) {
    talents.forEach((talent, index) => {
      if (playerSetup.talents[index].resource.source === "package" && talent && talent.worldRefs.length > 0 && !talent.worldRefs.includes(selectedWorldId)) {
        diagnostics.push(diagnostic("reference", "error", "PLAYER_SETUP_TALENT_WORLD_MISMATCH", `/talents/${index}/resource/resourceRef`, "/world/resourceRef"));
      }
    });
  }
  void world;
  return diagnostics;
}

export function validatePlayerSetup(value, cardPackage) {
  if (!validatePlayerSetupStructure(value)) return validationReport(schemaDiagnostics(validatePlayerSetupStructure, "PLAYER_SETUP"));
  const cardReport = validateCardPackage(cardPackage);
  if (!cardReport.valid) {
    return validationReport([diagnostic("reference", "error", "PLAYER_SETUP_CARD_PACKAGE_INVALID", "/cardPackageRef")]);
  }
  return validationReport(playerSetupSemanticDiagnostics(value, cardPackage));
}

function turnExchangeSemanticDiagnostics(turnExchange, cardPackage) {
  const diagnostics = [];
  if (turnExchange.cardPackageRef.id !== cardPackage.package.id ||
      turnExchange.cardPackageRef.version !== cardPackage.package.version) {
    diagnostics.push(diagnostic("reference", "error", "TURN_EXCHANGE_CARD_PACKAGE_REF_MISMATCH", "/cardPackageRef"));
  }

  const { indexes, all } = packageResourceIndexes(cardPackage);
  const commandIndex = indexes.commands;
  if (turnExchange.input.kind === "command" && !commandIndex.has(turnExchange.input.commandRef)) {
    diagnostics.push(diagnostic("reference", "error", "TURN_EXCHANGE_COMMAND_REF_MISSING", "/input/commandRef"));
  }
  turnExchange.proposal.suggestedActions.forEach((action, index) => {
    if (action.inputKind === "command" && !commandIndex.has(action.commandRef)) {
      diagnostics.push(diagnostic("reference", "error", "TURN_EXCHANGE_COMMAND_REF_MISSING", `/proposal/suggestedActions/${index}/commandRef`));
    }
  });
  diagnostics.push(...duplicateDiagnostics(turnExchange.proposal.suggestedActions, "/proposal/suggestedActions", "TURN_EXCHANGE_SUGGESTED_ACTION_ID_DUPLICATE"));

  const moduleRefs = new Map();
  turnExchange.proposal.informationModules.forEach((instance, index) => {
    const base = `/proposal/informationModules/${index}`;
    const prior = moduleRefs.get(instance.moduleRef);
    if (prior !== undefined) diagnostics.push(diagnostic("reference", "error", "TURN_EXCHANGE_INFORMATION_MODULE_DUPLICATE", `${base}/moduleRef`, prior));
    else moduleRefs.set(instance.moduleRef, `${base}/moduleRef`);
    const declaration = indexes.informationModules.get(instance.moduleRef)?.resource;
    if (!declaration) {
      diagnostics.push(diagnostic("reference", "error", "TURN_EXCHANGE_INFORMATION_MODULE_REF_MISSING", `${base}/moduleRef`));
      return;
    }
    const fields = new Map(declaration.fields.map((field) => [field.id, field]));
    const valueRefs = new Map();
    instance.values.forEach((entry, valueIndex) => {
      const valueBase = `${base}/values/${valueIndex}`;
      const first = valueRefs.get(entry.fieldRef);
      if (first !== undefined) diagnostics.push(diagnostic("reference", "error", "TURN_EXCHANGE_INFORMATION_FIELD_DUPLICATE", `${valueBase}/fieldRef`, first));
      else valueRefs.set(entry.fieldRef, `${valueBase}/fieldRef`);
      const field = fields.get(entry.fieldRef);
      if (!field) {
        diagnostics.push(diagnostic("reference", "error", "TURN_EXCHANGE_INFORMATION_FIELD_REF_MISSING", `${valueBase}/fieldRef`, `${base}/moduleRef`));
        return;
      }
      const matches = (field.valueType === "text" && typeof entry.value === "string") ||
        (field.valueType === "number" && typeof entry.value === "number" && Number.isFinite(entry.value)) ||
        (field.valueType === "boolean" && typeof entry.value === "boolean") ||
        (field.valueType === "list" && Array.isArray(entry.value) && entry.value.every((item) => typeof item === "string"));
      if (!matches) diagnostics.push(diagnostic("policy", "error", "TURN_EXCHANGE_INFORMATION_VALUE_TYPE", `${valueBase}/value`, `${base}/moduleRef`));
    });
  });

  if (turnExchange.input.kind === "query" && turnExchange.proposal.stateProposals.length > 0) {
    diagnostics.push(diagnostic("policy", "error", "TURN_EXCHANGE_QUERY_STATE_PROPOSAL_FORBIDDEN", "/proposal/stateProposals/0", "/input/kind"));
  }
  const stateFields = new Map(cardPackage.stateFields.map((field, index) => [field.id, { field, path: `/stateFields/${index}` }]));
  const proposedRefs = new Map();
  turnExchange.proposal.stateProposals.forEach((proposal, index) => {
    const base = `/proposal/stateProposals/${index}`;
    const first = proposedRefs.get(proposal.fieldRef);
    if (first !== undefined) diagnostics.push(diagnostic("reference", "error", "TURN_EXCHANGE_STATE_FIELD_DUPLICATE", `${base}/fieldRef`, first));
    else proposedRefs.set(proposal.fieldRef, `${base}/fieldRef`);
    const declaration = stateFields.get(proposal.fieldRef);
    if (!declaration) {
      diagnostics.push(diagnostic("reference", "error", "TURN_EXCHANGE_STATE_FIELD_REF_MISSING", `${base}/fieldRef`));
      return;
    }
    const { field, path: fieldPath } = declaration;
    if (!field.modelMayPropose) {
      diagnostics.push(diagnostic("policy", "error", "TURN_EXCHANGE_STATE_FIELD_NOT_PROPOSABLE", `${base}/fieldRef`, `${fieldPath}/modelMayPropose`));
    }
    const value = proposal.proposedValue;
    let matches = false;
    if (field.valueType === "boolean") matches = typeof value === "boolean";
    if (field.valueType === "integer") matches = Number.isInteger(value);
    if (field.valueType === "shortText") matches = typeof value === "string";
    if (field.valueType === "enum") matches = typeof value === "string";
    if (!matches) {
      diagnostics.push(diagnostic("policy", "error", "TURN_EXCHANGE_STATE_VALUE_TYPE", `${base}/proposedValue`, `${fieldPath}/valueType`));
      return;
    }
    if (field.valueType === "integer" &&
        ((field.minimum !== undefined && value < field.minimum) || (field.maximum !== undefined && value > field.maximum))) {
      diagnostics.push(diagnostic("policy", "error", "TURN_EXCHANGE_STATE_VALUE_RANGE", `${base}/proposedValue`, fieldPath));
    }
    if (field.valueType === "shortText" && value.length > field.maxLength) {
      diagnostics.push(diagnostic("policy", "error", "TURN_EXCHANGE_STATE_VALUE_LENGTH", `${base}/proposedValue`, `${fieldPath}/maxLength`));
    }
    if (field.valueType === "enum" && !field.choices.includes(value)) {
      diagnostics.push(diagnostic("policy", "error", "TURN_EXCHANGE_STATE_VALUE_ENUM", `${base}/proposedValue`, `${fieldPath}/choices`));
    }
  });

  const uncertaintyCodes = new Map();
  turnExchange.proposal.uncertainties.forEach((uncertainty, index) => {
    const path = `/proposal/uncertainties/${index}/code`;
    if (uncertaintyCodes.has(uncertainty.code)) {
      diagnostics.push(diagnostic("reference", "error", "TURN_EXCHANGE_UNCERTAINTY_CODE_DUPLICATE", path, uncertaintyCodes.get(uncertainty.code)));
    } else uncertaintyCodes.set(uncertainty.code, path);
    uncertainty.relatedResourceRefs.forEach((reference, offset) => {
      if (!all.has(reference)) diagnostics.push(diagnostic("reference", "error", "TURN_EXCHANGE_RESOURCE_REF_MISSING", `/proposal/uncertainties/${index}/relatedResourceRefs/${offset}`));
    });
  });
  return diagnostics;
}

export function validateTurnExchange(value, cardPackage) {
  if (!validateTurnExchangeStructure(value)) return validationReport(schemaDiagnostics(validateTurnExchangeStructure, "TURN_EXCHANGE"));
  const cardReport = validateCardPackage(cardPackage);
  if (!cardReport.valid) {
    return validationReport([diagnostic("reference", "error", "TURN_EXCHANGE_CARD_PACKAGE_INVALID", "/cardPackageRef")]);
  }
  return validationReport(turnExchangeSemanticDiagnostics(value, cardPackage));
}

function pluginManifestSemanticDiagnostics(manifest, manifestPath) {
  const diagnostics = [];
  const settingKeys = new Map();
  manifest.settings.forEach((setting, index) => {
    const keyPath = `${manifestPath}/settings/${index}/key`;
    if (settingKeys.has(setting.key)) diagnostics.push(diagnostic("reference", "error", "PLUGIN_MANIFEST_SETTING_KEY_DUPLICATE", keyPath, settingKeys.get(setting.key)));
    else settingKeys.set(setting.key, keyPath);
    if (setting.valueType === "integer" && setting.minimum !== undefined && setting.maximum !== undefined && setting.minimum > setting.maximum) {
      diagnostics.push(diagnostic("policy", "error", "PLUGIN_MANIFEST_SETTING_RANGE_INVALID", `${manifestPath}/settings/${index}/minimum`, `${manifestPath}/settings/${index}/maximum`));
    }
  });
  const dependencyIds = new Map();
  manifest.dependencies.forEach((dependency, index) => {
    const idPath = `${manifestPath}/dependencies/${index}/pluginId`;
    if (dependency.pluginId === manifest.plugin.id) {
      diagnostics.push(diagnostic("reference", "error", "PLUGIN_MANIFEST_SELF_DEPENDENCY", idPath, `${manifestPath}/plugin/id`));
    }
    if (dependencyIds.has(dependency.pluginId)) diagnostics.push(diagnostic("reference", "error", "PLUGIN_MANIFEST_DEPENDENCY_DUPLICATE", idPath, dependencyIds.get(dependency.pluginId)));
    else dependencyIds.set(dependency.pluginId, idPath);
  });
  const networkPermission = manifest.permissions.includes("network.request");
  if (manifest.network.mode === "modelmirror-mediated" && !networkPermission) {
    diagnostics.push(diagnostic("policy", "error", "PLUGIN_MANIFEST_NETWORK_PERMISSION_REQUIRED", `${manifestPath}/network/mode`, `${manifestPath}/permissions`));
  }
  if (manifest.network.mode === "none" && networkPermission) {
    diagnostics.push(diagnostic("policy", "error", "PLUGIN_MANIFEST_NETWORK_SCOPE_REQUIRED", `${manifestPath}/permissions`, `${manifestPath}/network/mode`));
  }
  return diagnostics;
}

function fallbackSuffix(fallback) {
  if (fallback === "readOnly") return "READ_ONLY";
  return fallback.toUpperCase();
}

function contextualManifestDiagnostics(items, required, fallback) {
  if (required) return items;
  const fallbackPart = `_FALLBACK_${fallbackSuffix(fallback)}`;
  return items.map((item) => diagnostic(
    item.phase,
    "warning",
    `PLUGIN_RECOMMENDED_${item.code.replace(/^PLUGIN_/u, "")}${fallbackPart}`,
    item.path,
    item.relatedPath,
  ));
}

function indexManifestCandidates(manifests) {
  const candidatesById = new Map();
  manifests.forEach((manifest, index) => {
    const manifestPath = `/manifests/${index}`;
    let pluginId;
    try {
      pluginId = manifest?.plugin?.id;
    } catch {
      return;
    }
    if (typeof pluginId !== "string") return;
    let candidateDiagnostics;
    try {
      const structureValid = validatePluginManifestStructure(manifest);
      candidateDiagnostics = structureValid
        ? pluginManifestSemanticDiagnostics(manifest, manifestPath)
        : schemaDiagnostics(validatePluginManifestStructure, "PLUGIN_MANIFEST", manifestPath);
    } catch {
      candidateDiagnostics = [diagnostic("schema", "error", "PLUGIN_MANIFEST_SCHEMA_VALIDATION_FAILED", manifestPath)];
    }
    const candidates = candidatesById.get(pluginId) ?? [];
    candidates.push({ manifest, path: manifestPath, diagnostics: candidateDiagnostics });
    candidatesById.set(pluginId, candidates);
  });
  return candidatesById;
}

function resolveManifestCandidate(pluginId, candidatesById, required, fallback, diagnostics) {
  const candidates = candidatesById.get(pluginId);
  if (!candidates || candidates.length === 0) return null;
  if (candidates.length > 1) {
    const duplicate = diagnostic(
      "reference",
      "error",
      "PLUGIN_MANIFEST_ID_DUPLICATE",
      `${candidates[1].path}/plugin/id`,
      `${candidates[0].path}/plugin/id`,
    );
    diagnostics.push(...contextualManifestDiagnostics([duplicate], required, fallback));
    return null;
  }
  const [candidate] = candidates;
  diagnostics.push(...contextualManifestDiagnostics(candidate.diagnostics, required, fallback));
  return candidate.diagnostics.length === 0 ? candidate : null;
}

function requirementResolution(requirement, requirementPath, candidatesById, required, fallback) {
  const diagnostics = [];
  const severity = required ? "error" : "warning";
  const kind = required ? "REQUIRED" : "RECOMMENDED";
  const fallbackPart = required ? "" : `_FALLBACK_${fallbackSuffix(fallback)}`;
  if (!candidatesById.has(requirement.pluginId)) {
    diagnostics.push(diagnostic("readiness", severity, `PLUGIN_${kind}_MISSING${fallbackPart}`, `${requirementPath}/pluginId`));
    return { diagnostics, candidate: null };
  }
  const candidate = resolveManifestCandidate(requirement.pluginId, candidatesById, required, fallback, diagnostics);
  if (!candidate) return { diagnostics, candidate: null };
  let compatible = true;
  if (candidate.manifest.plugin.version !== requirement.version) {
    compatible = false;
    diagnostics.push(diagnostic("readiness", severity, `PLUGIN_${kind}_VERSION_MISMATCH${fallbackPart}`, `${requirementPath}/version`, `${candidate.path}/plugin/version`));
  }
  requirement.capabilities.forEach((capability, index) => {
    if (!candidate.manifest.capabilities.includes(capability)) {
      compatible = false;
      diagnostics.push(diagnostic("readiness", severity, `PLUGIN_${kind}_CAPABILITY_MISSING${fallbackPart}`, `${requirementPath}/capabilities/${index}`, `${candidate.path}/capabilities`));
    }
  });
  return { diagnostics, candidate: compatible ? candidate : null };
}

function dependencyDiagnostics(rootCandidate, candidatesById, required, fallback) {
  const diagnostics = [];
  const severity = required ? "error" : "warning";
  const fallbackPart = required ? "" : `_FALLBACK_${fallbackSuffix(fallback)}`;
  const dependencyCode = (suffix) => required
    ? `PLUGIN_DEPENDENCY_${suffix}`
    : `PLUGIN_RECOMMENDED_DEPENDENCY_${suffix}${fallbackPart}`;
  const visiting = new Set();
  const visited = new Set();

  function visit(candidate) {
    const pluginId = candidate.manifest.plugin.id;
    if (visited.has(pluginId)) return;
    visiting.add(pluginId);
    candidate.manifest.dependencies.forEach((dependency, index) => {
      const base = `${candidate.path}/dependencies/${index}`;
      if (!candidatesById.has(dependency.pluginId)) {
        diagnostics.push(diagnostic("readiness", severity, dependencyCode("MISSING"), `${base}/pluginId`));
        return;
      }
      const target = resolveManifestCandidate(dependency.pluginId, candidatesById, required, fallback, diagnostics);
      if (!target) return;
      let compatible = true;
      if (target.manifest.plugin.version !== dependency.version) {
        compatible = false;
        diagnostics.push(diagnostic("readiness", severity, dependencyCode("VERSION_MISMATCH"), `${base}/version`, `${target.path}/plugin/version`));
      }
      dependency.capabilities.forEach((capability, capabilityIndex) => {
        if (!target.manifest.capabilities.includes(capability)) {
          compatible = false;
          diagnostics.push(diagnostic("readiness", severity, dependencyCode("CAPABILITY_MISSING"), `${base}/capabilities/${capabilityIndex}`, `${target.path}/capabilities`));
        }
      });
      if (!compatible) return;
      if (visiting.has(dependency.pluginId)) {
        diagnostics.push(diagnostic("readiness", severity, dependencyCode("CYCLE"), `${base}/pluginId`, `${target.path}/plugin/id`));
        return;
      }
      visit(target);
    });
    visiting.delete(pluginId);
    visited.add(pluginId);
  }
  visit(rootCandidate);
  return diagnostics;
}

function readinessReport(items) {
  const diagnostics = normalizeDiagnostics(items);
  return Object.freeze({
    ready: !diagnostics.some((item) => item.severity === "error"),
    diagnostics,
  });
}

export function evaluatePluginReadiness(cardPackage, manifests) {
  const cardReport = validateCardPackage(cardPackage);
  if (!cardReport.valid) {
    return readinessReport([diagnostic("readiness", "error", "PLUGIN_READINESS_CARD_PACKAGE_INVALID", "")]);
  }
  if (!Array.isArray(manifests)) {
    return readinessReport([diagnostic("schema", "error", "PLUGIN_READINESS_MANIFESTS_TYPE", "/manifests")]);
  }

  const diagnostics = [];
  const candidatesById = indexManifestCandidates(manifests);
  cardPackage.requiredPlugins.forEach((requirement, index) => {
    const resolution = requirementResolution(requirement, `/requiredPlugins/${index}`, candidatesById, true);
    diagnostics.push(...resolution.diagnostics);
    if (resolution.candidate) diagnostics.push(...dependencyDiagnostics(resolution.candidate, candidatesById, true));
  });
  cardPackage.recommendedPlugins.forEach((requirement, index) => {
    const resolution = requirementResolution(requirement, `/recommendedPlugins/${index}`, candidatesById, false, requirement.fallback);
    diagnostics.push(...resolution.diagnostics);
    if (resolution.candidate) diagnostics.push(...dependencyDiagnostics(
      resolution.candidate,
      candidatesById,
      false,
      requirement.fallback,
    ));
  });
  return readinessReport(diagnostics);
}
