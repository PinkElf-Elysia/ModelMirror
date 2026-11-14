import {project} from './observations.mjs';
// Trusted, concrete R6 card adapter; the task coordinator knows no item schema.
// This adapter reuses the existing R5 rule, not a second arithmetic implementation.
export const cultivationWorldPort={
 id:'cultivation-observation',version:'1',
 confirmedAction:{actionId:'use_medicine',itemId:'medicine',quantity:2},
 project,
 summary(state){return {medicine:state.items.find(x=>x.id==='medicine').quantity};}
};
