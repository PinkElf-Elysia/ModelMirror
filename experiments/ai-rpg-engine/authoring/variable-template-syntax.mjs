import Handlebars from 'handlebars';
import {fail} from './contracts.mjs';
const PANEL_LIMITS={templateChars:200000};
const helpers = new Set(['if','unless','each','with','eq','ne','gt','gte','lt','lte','and','or','not']);
export function checkPanelTemplate(template) {
  if (typeof template!=='string' || !template.isWellFormed() || [...template].length>PANEL_LIMITS.templateChars) fail('PANEL_TEMPLATE_CAPACITY');
  const h=Handlebars.create();let ast;
  try { ast=h.parse(template); } catch { fail('PANEL_TEMPLATE_SYNTAX'); }
  let nodes=0;
  function inspect(value,depth=0) {
    if (!value || typeof value!=='object') return;
    if(++nodes>20000 || depth>64)fail('PANEL_TEMPLATE_CAPACITY');
    if (['PartialStatement','PartialBlockStatement','Decorator','DecoratorBlock'].includes(value.type)) fail('PANEL_TEMPLATE_UNSUPPORTED');
    if (value.type==='PathExpression' && value.parts.some(p=>['__proto__','prototype','constructor','lookup','log','helperMissing','blockHelperMissing'].includes(p))) fail('PANEL_TEMPLATE_UNSUPPORTED');
    if (['BlockStatement','SubExpression'].includes(value.type) && !helpers.has(value.path.original)) fail('PANEL_TEMPLATE_UNSUPPORTED');
    if (value.type==='MustacheStatement' && value.params.length && !helpers.has(value.path.original)) fail('PANEL_TEMPLATE_UNSUPPORTED');
    for (const [key,child] of Object.entries(value)) if(key!=='loc') inspect(child,depth+1);
  }
  inspect(ast); return ast;
}
