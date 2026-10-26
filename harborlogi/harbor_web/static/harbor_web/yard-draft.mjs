/** Local validated edit history; never writes a plan or dispatches robot work. */
import {cloneBoxes, moveBox, validatePlacement} from './yard-model.mjs';

const coordinates = ['area','row','column','level','orientation'];
const samePlace = (a,b) => coordinates.every(key => a[key] === b[key]);
const sameShape = (a,b) => ['width','depth','footprint_revision','size'].every(key => a[key] === b[key]);

export class DraftSession {
  constructor(areas, base) {
    this.areas = areas;
    this.history = [cloneBoxes(base)];
    this.cursor = 0;
  }
  get boxes() { return this.history[this.cursor]; }
  get frames() { return this.history.slice(0,this.cursor+1); }
  get changes() {
    return this.boxes.flatMap(box => {
      const before = this.history[0].find(item => item.id === box.id);
      return samePlace(before,box) ? [] : [{before,after:box}];
    });
  }
  move(id, placement) {
    const result = moveBox(this.areas,this.boxes,id,placement);
    if (result.valid && !samePlace(this.boxes.find(box=>box.id===id),result.boxes.find(box=>box.id===id))) {
      this.history.splice(this.cursor+1);
      this.history.push(result.boxes); this.cursor++;
    }
    return result;
  }
  undo() { if (!this.cursor) return false; this.cursor--; return true; }
  redo() { if (this.cursor === this.history.length-1) return false; this.cursor++; return true; }
  load(frames) {
    const reject = () => ({valid:false,reason:'저장된 이동과 원본 화물·크기·지지 조건을 확인할 수 없습니다. 원래 계획에서 다시 검토하세요.'});
    const base = this.history[0], staged = new DraftSession(this.areas,base);
    if (!frames.length || frames.some(values => !validatePlacement(this.areas,values).valid || values.length !== base.length ||
      values.some(box => !base.some(item => item.id === box.id && sameShape(item,box))))) return reject();
    if (!frames[0].every(box => samePlace(box,base.find(item=>item.id===box.id)))) return reject();
    for (const values of frames.slice(1)) {
      const changed = values.filter(box => !samePlace(box,staged.boxes.find(item=>item.id===box.id)));
      if (changed.length > 1 || (changed.length && !staged.move(changed[0].id,changed[0]).valid)) return reject();
    }
    this.history = staged.history; this.cursor = staged.cursor;
    return {valid:true,reason:''};
  }
}
