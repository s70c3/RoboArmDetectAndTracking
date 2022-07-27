import argparse

import os

# limit the number of cpus used by high performance libraries
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

import sys
from pathlib import Path
import torch
import torch.backends.cudnn as cudnn

FILE = Path(__file__).resolve()
ROOT = FILE.parents[0]  # yolov5 strongsort root directory
WEIGHTS = ROOT / 'weights'

if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))  # add ROOT to PATH
if str(ROOT / 'yolov5') not in sys.path:
    sys.path.append(str(ROOT / 'yolov5'))  # add yolov5 ROOT to PATH
if str(ROOT / 'strong_sort') not in sys.path:
    sys.path.append(str(ROOT / 'strong_sort'))  # add strong_sort ROOT to PATH
ROOT = Path(os.path.relpath(ROOT, Path.cwd()))  # relative

import logging
from yolov5.models.common import DetectMultiBackend
from yolov5.utils.dataloaders import VID_FORMATS, LoadImages, LoadStreams
from yolov5.utils.general import (LOGGER, check_img_size, non_max_suppression, scale_coords, check_requirements, cv2,
                                  check_imshow, xyxy2xywh, increment_path, strip_optimizer, colorstr, print_args,
                                  check_file)
from yolov5.utils.torch_utils import select_device, time_sync
from yolov5.utils.plots import Annotator, colors, save_one_box
from strong_sort.utils.parser import get_config
from strong_sort.strong_sort import StrongSORT

# remove duplicated stream handler to avoid duplicated logging
logging.getLogger().removeHandler(logging.getLogger().handlers[0])

def check_path(source):
    is_file = Path(source).suffix[1:] in (VID_FORMATS)
    is_url = source.lower().startswith(('rtsp://', 'rtmp://', 'http://', 'https://'))
    webcam = source.isnumeric() or source.endswith('.txt') or (is_url and not is_file)
    return is_file, is_url, webcam

@torch.no_grad()
def run(
        source_front='0',
        source_side='0',
        yolo_weights=WEIGHTS / 'yolov5m.pt',  # model.pt path(s),
        strong_sort_weights=WEIGHTS / 'osnet_x0_25_msmt17.pt',  # model.pt path,
        config_strongsort=ROOT / 'strong_sort/configs/strong_sort.yaml',
        imgsz=(640, 640),  # inference size (height, width)
        conf_thres=0.25,  # confidence threshold
        iou_thres=0.45,  # NMS IOU threshold
        max_det=1000,  # maximum detections per image
        device='',  # cuda device, i.e. 0 or 0,1,2,3 or cpu
        show_vid=False,  # show results
        save_conf=False,  # save confidences in --save-txt labels
        save_vid=False,  # save confidences in --save-txt labels
        classes=None,  # filter by class: --class 0, or --class 0 2 3
        agnostic_nms=False,  # class-agnostic NMS
        augment=False,  # augmented inference
        visualize=False,  # visualize features
        update=False,  # update all models
        project=ROOT / 'runs/track',  # save results to project/name
        name='exp',  # save results to project/name
        exist_ok=False,  # existing project/name ok, do not increment
        line_thickness=3,  # bounding box thickness (pixels)
        hide_labels=False,  # hide labels
        hide_conf=False,  # hide confidences
        hide_class=False,  # hide IDs
        half=False,  # use FP16 half-precision inference
):
    source_front = str(source_front)
    source_side = str(source_side)

    is_url_f, is_file_f, webcam_f = check_path(source_front)

    is_url_s, is_file_s, webcam_s = check_path(source_side)

    # Directories
    if not isinstance(yolo_weights, list):  # single yolo model
        exp_name = str(yolo_weights).rsplit('/', 1)[-1].split('.')[0]
    elif type(yolo_weights) is list and len(yolo_weights) == 1:  # single models after --yolo_weights
        exp_name = yolo_weights[0].split(".")[0]
    else:  # multiple models after --yolo_weights
        exp_name = 'ensemble'
    exp_name = name if name is not None else exp_name + "_" + str(strong_sort_weights).split('/')[-1].split('.')[0]
    save_dir = increment_path(Path(project) / exp_name, exist_ok=exist_ok)  # increment run
    save_dir.mkdir(parents=True, exist_ok=True)  # make dir

    # Load model
    device = select_device(device)
    model = DetectMultiBackend(yolo_weights, device=device, data=None, fp16=half)
    stride, names, pt = model.stride, model.names, model.pt
    imgsz = check_img_size(imgsz, s=stride)  # check image size

    # Dataloader
    if webcam_f:
        show_vid = check_imshow()
        cudnn.benchmark = True  # set True to speed up constant image size inference
        dataset = LoadStreams(source_front, img_size=imgsz, stride=stride, auto=pt)
        nr_sources = len(dataset)
    else:
        dataset = LoadImages(source_front, source_side, img_size=imgsz, stride=stride, auto=pt)
        nr_sources = 1
    LOGGER.info(f'Dataset {dataset}')
    vid_path, vid_writer, txt_path = [None] * nr_sources, [None] * nr_sources, [None] * nr_sources

    # initialize StrongSORT
    cfg = get_config()
    cfg.merge_from_file(opt.config_strongsort)

    # Create as many strong sort instances as there are video sources
    strongsort_list = []
    for i in range(nr_sources):
        strongsort_list.append(
            StrongSORT(
                strong_sort_weights,
                device,
                max_dist=cfg.STRONGSORT.MAX_DIST,
                max_iou_distance=cfg.STRONGSORT.MAX_IOU_DISTANCE,
                max_age=cfg.STRONGSORT.MAX_AGE,
                n_init=cfg.STRONGSORT.N_INIT,
                nn_budget=cfg.STRONGSORT.NN_BUDGET,
                mc_lambda=cfg.STRONGSORT.MC_LAMBDA,
                ema_alpha=cfg.STRONGSORT.EMA_ALPHA,

            )
        )
    outputs = [None] * nr_sources

    # Run tracking
    model.warmup(imgsz=(1 if pt else nr_sources, 3, *imgsz))  # warmup
    dt, seen = [0.0, 0.0, 0.0, 0.0], 0
    curr_frames, prev_frames = [None] * nr_sources, [None] * nr_sources
    for frame_idx, (path, im, im0s, vid_cap_f, vid_cap_s , s) in enumerate(dataset):
        t1 = time_sync()
        im = torch.from_numpy(im).to(device)
        im = im.half() if half else im.float()  # uint8 to fp16/32
        im /= 255.0  # 0 - 255 to 0.0 - 1.0
        if len(im.shape) == 3:
            im = im[None]  # expand for batch dim
        t2 = time_sync()
        dt[0] += t2 - t1

        # Inference
        visualize = increment_path(save_dir / Path(path[0]).stem, mkdir=True) if opt.visualize else False
        pred = model(im, augment=opt.augment, visualize=visualize)
        t3 = time_sync()
        dt[1] += t3 - t2

        # Apply NMS
        pred = non_max_suppression(pred, opt.conf_thres, opt.iou_thres, opt.classes, opt.agnostic_nms,
                                   max_det=opt.max_det)
        dt[2] += time_sync() - t3

        # Process detections
        if len(pred) > 4:
            raise ValueError('Something went wrong. Detected more than 2 sockets and 2 ends')
        ends = []
        sockets = []
        for i, det in enumerate(pred):  # detections per image
            seen += 1
            if webcam_f:  # nr_sources >= 1
                p, im0, _ = path[i], im0s[i].copy(), dataset.count
                p = Path(p)  # to Path
                s += f'{i}: '
                txt_file_name = p.name
                save_path = str(save_dir / p.name)  # im.jpg, vid.mp4, ...
            else:
                p, im0, _ = path, im0s.copy(), getattr(dataset, 'frame', 0)
                p = Path(p[0])
                # to Path
                # video file
                if source_front.endswith(VID_FORMATS):
                    txt_file_name = p.stem
                    save_path = str(save_dir / p.name)  # im.jpg, vid.mp4, ...
                # folder with imgs
                else:
                    txt_file_name = p.parent.name  # get folder name containing current img
                    save_path = str(save_dir / p.parent.name)  # im.jpg, vid.mp4, ...
            curr_frames[i] = im0


            s += '%gx%g ' % im.shape[2:]  # print string

            annotator = Annotator(im0, line_width=2, pil=not ascii)
            if cfg.STRONGSORT.ECC:  # camera motion compensation
                strongsort_list[i].tracker.camera_update(prev_frames[i], curr_frames[i])

            if det is not None and len(det):
                # Rescale boxes from img_size to im0 size
                det[:, :4] = scale_coords(im.shape[2:], det[:, :4], im0.shape).round()

                # Print results
                for c in det[:, -1].unique():
                    n = (det[:, -1] == c).sum()  # detections per class
                    s += f"{n} {names[int(c)]}{'s' * (n > 1)}, "  # add to string

                xywhs = xyxy2xywh(det[:, 0:4])
                confs = det[:, 4]
                clss = det[:, 5]

                # pass detections to strongsort
                t4 = time_sync()
                outputs[i] = strongsort_list[i].update(xywhs.cpu(), confs.cpu(), clss.cpu(), im0)
                t5 = time_sync()
                dt[3] += t5 - t4

                # draw boxes for visualization
                if len(outputs[i]) > 0:

                    for j, (output, conf) in enumerate(zip(outputs[i], confs)):

                        bboxes = output[0:4]
                        id = output[4]
                        cls = output[5]
                        if cls == 0:
                            ends.append(bboxes)
                        else:
                            sockets.append(bboxes)
                        if save_vid  or show_vid:  # Add bbox to image
                            c = int(cls)  # integer class
                            id = int(id)  # integer id
                            label = None if hide_labels else (f'{id} {names[c]}' if hide_conf else \
                                                                  (
                                                                      f'{id} {conf:.2f}' if hide_class else f'{id} {names[c]} {conf:.2f}'))
                            annotator.box_label(bboxes, label, color=colors(c, True))


                LOGGER.info(f'{s}Done. YOLO:({t3 - t2:.3f}s), StrongSORT:({t5 - t4:.3f}s)')

            else:
                strongsort_list[i].increment_ages()
                LOGGER.info('No detections')
            #calc dist
            left_socket, right_socket, left_end, right_end = None, None, None, None
            if len(sockets) > 1:
                left_socket, right_socket = (sockets[0], sockets[1]) if sockets[0][0] < sockets[1][0] else (sockets[1], sockets[0])
            elif len(sockets)==1:
                left_socket = sockets[0] if sockets[0][0] < im0.shape[1]//2 else None
                right_socket = sockets[0] if sockets[0][0] > im0.shape[1] // 2 else None

            if len(ends) > 1:
                left_end, right_end = (ends[0], ends[1]) if ends[0][0] < ends[1][0] else (ends[1], ends[0])
            elif len(ends)==1:
                print(ends[0], im0.shape)
                left_end = ends[0] if ends[0][0] < im0.shape[1]//2 else None
                right_end = ends[0] if ends[0][0] > im0.shape[1] // 2 else None

            if left_socket is not None and left_end is not None:
                d = 100/(left_socket[3]-left_socket[1]) #mm/px
                left_s_h, left_s_v = (int(left_socket[0]+left_socket[2]))//2, int((left_socket[1]+left_socket[3]))//2
                left_e_h, left_e_v = (int(left_end[0]+left_end[2]))//2, (int(left_end[1]+left_end[3]))//2,
                h_dist_left = (left_s_h - left_e_h)
                v_dist_left = (left_s_v - left_e_v)

                cv2.line(im0, (left_s_h, left_s_v), (left_e_h, left_e_v), (0, 50, 255), 2)
                cv2.line(im0, (left_s_h, left_s_v), (left_s_h, left_e_v), (0, 50, 255), 3)
                cv2.line(im0, (left_s_h, left_e_v), (left_e_h, left_e_v), (0, 50, 255), 3)
                cv2.putText(im0, str(h_dist_left*d), color=(0, 255, 0), fontScale=1.5, thickness=3, fontFace=cv2.FONT_HERSHEY_SIMPLEX,
                            org=(50, 200))
                cv2.putText(im0, str(v_dist_left*d), color=(0, 255, 0), fontScale=1.5, thickness=3, fontFace=cv2.FONT_HERSHEY_SIMPLEX,
                            org=(50, 250))

            if right_socket is not None and right_end is not None:
                d = 100 / (right_socket[3] - right_socket[1])  # mm/px
                right_s_h, right_s_v = int((right_socket[0] + right_socket[2])) // 2, int((
                            right_socket[1] + right_socket[3])) // 2

                right_e_h, right_e_v = (int(right_end[0] + right_end[2]) // 2), (int(right_end[1] + right_end[3]) // 2),
                h_dist_right = (right_s_h - right_e_h)
                v_dist_right = (right_s_v - right_e_v)

                cv2.line(im0, (right_s_h, right_s_v), (right_e_h, right_e_v), (0, 50, 255), 3)
                cv2.line(im0, (right_s_h, right_s_v), (right_s_h, right_e_v), (0, 50, 255), 3)
                cv2.line(im0, (right_s_h, right_e_v), (right_e_h, right_e_v), (0, 50, 255), 3)

                cv2.putText(im0, str(h_dist_right*d), color=(0, 255, 0), fontScale=1.5,thickness=3, fontFace=cv2.FONT_HERSHEY_SIMPLEX,
                            org=(im0.shape[1]//2, 200))
                cv2.putText(im0, str(v_dist_right*d), color=(0, 255, 0), fontScale=1.5, thickness=3, fontFace=cv2.FONT_HERSHEY_SIMPLEX,
                            org=(im0.shape[1]//2, 250))
            # Stream results
            im0 = annotator.result()
            if show_vid:
                cv2.imshow(str(p), im0)
                cv2.waitKey(1)  # 1 millisecond

            # Save results (image with detections)
            if save_vid:
                if vid_path[i] != save_path:  # new video
                    vid_path[i] = save_path
                    if isinstance(vid_writer[i], cv2.VideoWriter):
                        vid_writer[i].release()  # release previous video writer
                    if vid_cap:  # video
                        fps = vid_cap.get(cv2.CAP_PROP_FPS)
                        w = int(vid_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                        h = int(vid_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    else:  # stream
                        fps, w, h = 30, im0.shape[1], im0.shape[0]
                    save_path = str(Path(save_path).with_suffix('.mp4'))  # force *.mp4 suffix on results videos
                    vid_writer[i] = cv2.VideoWriter(save_path, cv2.VideoWriter_fourcc(*'mp4v'), fps, (w, h))
                vid_writer[i].write(im0)

            prev_frames[i] = curr_frames[i]

    # Print results
    t = tuple(x / seen * 1E3 for x in dt)  # speeds per image
    LOGGER.info(
        f'Speed: %.1fms pre-process, %.1fms inference, %.1fms NMS, %.1fms strong sort update per image at shape {(1, 3, *imgsz)}' % t)
    if save_vid:
        s =  ''
        LOGGER.info(f"Results saved to {colorstr('bold', save_dir)}{s}")
    if update:
        strip_optimizer(yolo_weights)  # update model (to fix SourceChangeWarning)


def parse_opt():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-front', type=str, default='0', help='file/dir/URL/glob')
    parser.add_argument('--source-side', type=str, default='0', help='file/dir/URL/glob')
    # parser.add_argument('--source', type=str, default='0', help='file/dir/URL/glob')
    parser.add_argument('--yolo-weights', nargs='+', type=str, default=WEIGHTS / 'yolov5m.pt', help='model.pt path(s)')
    parser.add_argument('--strong-sort-weights', type=str, default=WEIGHTS / 'osnet_x0_25_msmt17.pt')

    parser.add_argument('--device', default='cpu', help='cuda device, i.e. 0 or 0,1,2,3 or cpu')

    parser.add_argument('--config-strongsort', type=str, default='strong_sort/configs/strong_sort.yaml')
    parser.add_argument('--imgsz', '--img', '--img-size', nargs='+', type=int, default=[640], help='inference size h,w')
    parser.add_argument('--conf-thres', type=float, default=0.5, help='confidence threshold')
    parser.add_argument('--iou-thres', type=float, default=0.5, help='NMS IoU threshold')
    parser.add_argument('--max-det', type=int, default=1000, help='maximum detections per image')
    parser.add_argument('--show-vid', action='store_true', help='display tracking video results')
    parser.add_argument('--save-conf', action='store_true', help='save confidences in --save-txt labels')
    parser.add_argument('--save-vid', action='store_true', help='save video tracking results')
    # class 0 is person, 1 is bycicle, 2 is car... 79 is oven
    parser.add_argument('--classes', nargs='+', type=int, help='filter by class: --classes 0, or --classes 0 2 3')
    parser.add_argument('--agnostic-nms', action='store_true', help='class-agnostic NMS')
    parser.add_argument('--augment', action='store_true', help='augmented inference')
    parser.add_argument('--visualize', action='store_true', help='visualize features')
    parser.add_argument('--update', action='store_true', help='update all models')
    parser.add_argument('--project', default=ROOT / 'runs/track', help='save results to project/name')
    parser.add_argument('--name', default='exp', help='save results to project/name')
    parser.add_argument('--exist-ok', action='store_true', help='existing project/name ok, do not increment')

    parser.add_argument('--line-thickness', default=3, type=int, help='bounding box thickness (pixels)')

    parser.add_argument('--hide-labels', default=False, action='store_true', help='hide labels')
    parser.add_argument('--hide-conf', default=False, action='store_true', help='hide confidences')
    parser.add_argument('--hide-class', default=False, action='store_true', help='hide IDs')
    opt = parser.parse_args()
    opt.imgsz *= 2 if len(opt.imgsz) == 1 else 1  # expand
    print_args(vars(opt))
    return opt


def main(opt):
    check_requirements(requirements=ROOT / 'requirements.txt', exclude=('tensorboard', 'thop'))
    run(**vars(opt))


if __name__ == "__main__":
    opt = parse_opt()
    main(opt)