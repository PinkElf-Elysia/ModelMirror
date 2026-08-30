import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseR20RunArguments } from "./lib/r20-cli-core.mjs";
import { launchR20Bridge } from "./qualify-r20-npc-bridge.mjs";
const temporaryRoot=path.resolve(path.parse(fileURLToPath(import.meta.url)).root,"tmp");
export async function runR20PreviewCli(args){return await launchR20Bridge(parseR20RunArguments(args,temporaryRoot),{headless:false,waitForTrace:false})}
if(fileURLToPath(import.meta.url)===path.resolve(process.argv[1]??"")){try{const result=await runR20PreviewCli(process.argv.slice(2));process.stdout.write(`MATRIX_OASIS_R20_NPC_BRIDGE_READY sourceRunId=${result.sourceRunId} actors=${result.bindingCount} qualificationStatus=${result.qualificationStatus} qualificationBasisManifestSha256=${result.qualificationBasisManifestSha256}\n`);let stopping=false;const stop=async(exitCode)=>{if(stopping)return;stopping=true;try{await result.cleanup();process.exitCode=exitCode}catch{process.exitCode=2}};result.child.once("error",()=>{void stop(2)});result.child.once("exit",(code,signal)=>{void stop(code===0&&signal===null?0:2)});process.once("SIGINT",()=>{void stop(0)});process.once("SIGTERM",()=>{void stop(0)})}catch(error){process.stderr.write(`${error?.message??"R20_PREVIEW_INTERNAL_ERROR"}\n`);process.exitCode=2}}
