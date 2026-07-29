export type BBox={x:number;y:number;width:number;height:number};
export type Field={field_id:string;label:string;field_type:string;bbox:BBox;raw_value:unknown;normalized_value:unknown;confidence:number;source:string;needs_review:boolean;validation_errors:{code:string;message:string}[]};
export type Run={run_id:string;template_id:string;template_match_confidence:number;status:string;pages:{image_url:string;width:number;height:number;alignment_confidence:number;fields:Field[]}[];summary:{field_count:number;review_count:number;processing_ms:number}};
