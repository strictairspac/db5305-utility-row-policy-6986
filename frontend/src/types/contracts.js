/** @typedef {{id:number, title:string, payload:object, input_digest:string, author:string, reused?:boolean}} BusinessCase */
/** @typedef {{id:number, case_id:number, status:'queued'|'running'|'complete'|'failed', reused?:boolean, reuse_reason?:string}} Run */
/** @typedef {{id:number, run_id:number, digest:string, report:{summary:string, metrics:object, sections:Array}}} Evidence */
