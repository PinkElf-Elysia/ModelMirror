export declare class V2QualificationOperationalError extends Error { readonly code: string; }
export declare function createCandidateLock(candidate: unknown): Readonly<unknown>;
export declare function verifyCandidateCheckout(request: { candidateLock: unknown; sourceDir: string }): Readonly<unknown>;
export declare function runBoundedCommand(request: unknown): Promise<Readonly<unknown>>;
export declare function publishQualification(request: unknown): Readonly<unknown>;
export declare function verifyQualificationDirectory(directory: string): Readonly<unknown>;
export declare function planCandidateQualification(candidate: unknown): Readonly<unknown>;
export declare function qualifySourceOnly(request: { candidate: unknown; sourceDir: string; outputDir: string }): Readonly<unknown>;
