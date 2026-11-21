import {join} from 'node:path';
import {hash, keys, need} from '../contracts.mjs';
import {Choices} from '../../feature-exploration/context-suggestions-e6/store.mjs';
import {bindChoiceSource, stamp} from '../../feature-exploration/context-suggestions-e6/source.mjs';

const scopeKey = choice => hash([choice.source, choice.id, choice.text]);
const operation = choice => 'choice-' + scopeKey(choice).slice(0, 40);
// R10 only supports the player consuming a named medicine. Ambiguous wording stays dialogue.
function clarifyMedicine(input, proposal, state) {
  const action = proposal.action;
  if (action.actionId !== 'use_medicine') return null;
  const match = /^(?:我(?:决定)?(?:自己|亲自)?|自己)\s*(?:服用|服下)\s*([1-9]\d*|[一二两三四五六七八九十])\s*份\s*([^，。！？!?\s]+)[。！!]?$/u.exec(input.trim());
  const item = state.items.find(entry => entry.id === action.itemId);
  if (match && match[2] === item?.name) {
    const count = /^\d+$/u.test(match[1]) ? Number(match[1]) : '〇一二三四五六七八九十'.indexOf(match[1].replace('两','二'));
    if (count === action.quantity) return null;
  }
  return '丹药仍按原数登记。赠药意向可以继续交流；如果要自己服药，请单独写明丹药名称和数量。';
}

// Gifts have no settlement rule in this scene. Adopt only an intent in that turn.
function medicineGiftIntent(input, state) {
  const mentionsMedicine = /丹药|药丸|服用|服药/u.test(input)
    || state.items.some(item => item.kind === '丹药' && input.includes(item.name));
  const transfer = /赠送|送给|递给|交给|分给|转赠|[丹药丸]给/u.test(input)
    || /给[^，。！？]{1,16}(?:服用|服下|吃药|[一二两三四五六七八九十\d]+\s*[份枚颗粒])/u.test(input);
  if (/(?:赠药|送药|递药)/u.test(input) || (mentionsMedicine && transfer)) {
    return '赠药仍是一个打算。丹药仍按原数登记。';
  }
  return null;
}

const waiting = new Set(['pending', 'running', 'unknown', 'awaiting-adoption']);

// Caller holds the scene lock until the source check and task admission both finish.
// Open the E6 journal for this run only; forked worlds start with an empty journal.
export class SceneChoices {
  constructor(runtime, directory) {
    this.runtime = runtime;
    this.directory = directory;
    this.sends = new Map();
    const cultivation = runtime.ports.get('cultivation');
    if (cultivation) cultivation.store.modelProposalPolicy = {
      instruction:'use_medicine 仅供玩家本人服药。只有玩家明确自服且药名、数量相符时才可准备该提案。赠药、他人服药和否定自服只作交流，有歧义先澄清。赠药意图只写打算，不能写成交付、服药或药效；没有赠药行动回执时保持关系、信任、印象和最近接触原值，赠药回合返回空增量。',
      intentOnly:medicineGiftIntent,
      clarify:clarifyMedicine
    };
  }

  async send(runId, data) {
    keys(data, ['key']);
    need(typeof data.key === 'string' && /^[a-f0-9]{64}$/.test(data.key), 'CHOICE_SCOPE');
    const key = runId + '/' + data.key;
    if (this.sends.has(key)) return this.sends.get(key);
    const pending = this.access(runId, 'choice-send', data);
    this.sends.set(key, pending);
    try { return await pending; } finally { this.sends.delete(key); }
  }

  async access(runId, action = 'view', data = {}) {
    const runtime = this.runtime;
    return runtime.exclusive(runId, async () => {
      const run = await runtime.run(runId);
      let scene = await runtime.view(runId);
      scene.busy = false; // The only lock holder is this operation.
      if (run.adapterId !== 'cultivation') {
        need(action === 'view', 'CHOICE_UNAVAILABLE');
        return scene;
      }
      const source = bindChoiceSource(async () => ({
        ...scene, busy: scene.tasks.some(t => waiting.has(t.adoption))
      }), runtime.port(run).adapter);
      const journal = await Choices.open(join(this.directory, run.id), source);
      try {
        if (action === 'choice-edit') {
          await journal.command(data);
        } else if (action === 'choice-send') {
          keys(data, ['key']);
          const {saved, frame} = await journal.view();
          const choice = saved.find(x => scopeKey(x) === data.key);
          need(choice, 'CHOICE_SCOPE');
          const ownerId = runtime.compiled.entrypoints.dialogue;
          const operationId = operation(choice);
          const existing = scene.tasks.find(t => t.operationId === operationId && t.purpose === ownerId);
          if (existing) {
            need(existing.input === choice.text, 'CHOICE_COLLISION');
          } else {
            need(!choice.plan, 'CHOICE_INTENT_ONLY');
            need(hash(choice.source) === stamp(frame), 'CHOICE_STALE');
            need(frame.ready, 'CHOICE_NOT_READY');
            await runtime.task(run, {ownerId, operationId, input: choice.text});
          }
        } else {
          need(action === 'view', 'CHOICE_COMMAND');
        }
        if (action === 'choice-send') scene = await runtime.view(runId);
        scene.busy = false;
        // Re-project the source after admission; a completed task can advance the world.
        const snapshot = await journal.view();
        const currentSource = stamp(snapshot.frame);
        const result = snapshot.result;
        scene.choices = {
          revision: snapshot.revision,
          result: result && {goal:result.goal, question:result.question, options:result.options, notice:result.notice},
          stale: !!result && result.source !== currentSource,
          saved: snapshot.saved.map(choice => {
            const operationId = operation(choice);
            const task = scene.tasks.find(t => t.operationId === operationId);
            const event = scene.world.events.find(e => e.receipt?.sourceProposal === operationId);
            return {...choice, key:scopeKey(choice), taskId:task?.id ?? null, status:task?.adoption ?? null,
              proposalStatus:scene.world.proposals[operationId]?.status ?? null,
              eventId:event?.id ?? null, stale:hash(choice.source) !== currentSource};
          })
        };
        return scene;
      } finally {
        await journal.close();
      }
    });
  }
}
