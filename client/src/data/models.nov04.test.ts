import {describe, expect, it} from "vitest";
import {models} from "./models";
const ids=["tencent/hy4","morph/reflex","cohere/embed-5-fast","cohere/embed-5-pro","cohere/rerank-5-fast","cohere/rerank-5-pro","cloudflare/clairic","moondream/parakeet-ultra","stealth/crouton-alpha","z-ai/glm-5.3-flashx-legal","tbc/mischief-1","baseten/joey-1","sourceful/riverflow-v2.6-pro","mongodb/rerank-3-lite","mongodb/rerank-3","mongodb/voyage-code-4","mongodb/rerank-2.5-lite","mongodb/rerank-2.5","mongodb/voyage-multimodal-3.5","mongodb/voyage-4-lite","mongodb/voyage-4","mongodb/voyage-4-large"];
describe("November 4 catalog contracts",()=>{
 it.each(ids)("places %s once below six rows",id=>{expect(models.filter(m=>m.id===id)).toHaveLength(1);expect(models.findIndex(m=>m.id===id)).toBeGreaterThanOrEqual(20);});
 it.each(["morph/reflex","cloudflare/clairic","baseten/joey-1"])("uses decisions for %s",id=>{expect(models.find(m=>m.id===id)?.ui_entrypoint).toBe("decisions");});
 it.each(ids.filter(id=>id.includes('rerank')))("does not mark %s free",id=>{expect(models.find(m=>m.id===id)?.pricing_status).not.toBe("free");expect(models.find(m=>m.id===id)?.note).toContain("/百万 Token");});
 it("keeps transcription priced by duration",()=>{expect(models.find(m=>m.id==="moondream/parakeet-ultra")).toMatchObject({pricing_basis:"media",media_pricing:{unit:"audio_hour",usd:0.0000166667*3600}});});
 it("keeps dynamic image prices and mandatory video reference explicit",()=>{expect(models.find(m=>m.id==="sourceful/riverflow-v2.6-pro")?.note).toContain("动态");expect(models.find(m=>m.id==="tbc/mischief-1")?.note).toContain("首帧必需");});
});
