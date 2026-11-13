// Published players keep independent selections per immutable version. The original
// workbench key remains unchanged, preserving existing local session references.
export const modelSessionKey=(workId,versionId=null)=>'modelmirror.authoring.model-session/'+workId+(versionId?'/'+versionId:'');
