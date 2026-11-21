/** Render fixed slots in photo orientation; logical placements and robot addresses stay intact. */
export const transposePoint = ({x, y, z}) => ({x:y, y:x, z});
export const transposeArea = area => ({...area, x:area.y, y:area.x, rows:area.columns, columns:area.rows});
export const transposeBox = box => ({...box, x:box.y, y:box.x, width:box.depth, depth:box.width});
