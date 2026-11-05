import {describe,expect,it} from "vitest";
import {models} from "./models";
import {estimateImageCost,GPT_IMAGE_2_5_TOKEN_PRICING_BY_MODEL_ID,imageTokenPricingSummary} from "../utils/imageCostEstimate";
describe("GPT Image 2.6 replacement",()=>{
 it.each(["flare","sunburst"])("replaces %s front placement without removing 2.5",tier=>{
  const id=`openai/gpt-image-2.6-${tier}`;
  expect(models.filter(m=>m.id===id)).toHaveLength(1);
  expect(models.findIndex(m=>m.id===id)).toBeLessThan(20);
  expect(models.findIndex(m=>m.id===`openai/gpt-image-2.5-${tier}`)).toBeGreaterThanOrEqual(20);
  expect(models.find(m=>m.id===id)).toMatchObject({ui_entrypoint:"multimodal",primary_operation:"generate_image",pricing_basis:"media"});
  const pricing=GPT_IMAGE_2_5_TOKEN_PRICING_BY_MODEL_ID[id];
  expect(estimateImageCost(pricing,{outputCount:10,referenceCount:16})).toBeNull();
  expect(imageTokenPricingSummary(pricing)).toBe("文本输入 $5/M Token · 图片输入 $8/M Token · 图片输出 $30/M Token");
 });
 it("adds Ripple as normal text/tool chat after sixth row",()=>{
  const id="ricursiveai/ripple-65b-preview";
  expect(models.find(m=>m.id===id)).toMatchObject({ui_entrypoint:"chat",context_length:1048576,supported_parameters:expect.arrayContaining(["tools","tool_choice"])});
  expect(models.findIndex(m=>m.id===id)).toBeGreaterThanOrEqual(20);
 });
});
