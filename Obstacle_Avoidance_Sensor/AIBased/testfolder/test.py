from ultralytics import YOLO
import cv2
import math 
import logging
logging.getLogger("ultralytics").setLevel(logging.WARNING)

#image 
img_path = cv2.imread("../sideworkandpo.jpg")
img = cv2.resize(img_path, (640,640))

# model
model = YOLO("pothole.pt")

# object classes
classNames = ["pothole"]


while True:
    results = model(img, stream=True)

    #coordinates
    for r in results:
        boxes = r.boxes

        for box in boxes:
            # bounding box
            x1, y1, x2, y2 = box.xyxy[0]
            x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2) # convert to int values

            # put box in cam
            cv2.rectangle(img, (x1, y1), (x2, y2), (255, 0, 255), 3)

            # confidence
            confidence = math.ceil((box.conf[0]*100))/100
            print("Confidence --->",confidence)

            # class name
            cls = int(box.cls[0])
            print("Class name -->", classNames[cls])

            # object details
            org = [x1, y1]
            font = cv2.FONT_HERSHEY_SIMPLEX
            fontScale = 1
            color = (255, 0, 0)
            thickness = 2

            cv2.putText(img, classNames[cls], org, font, fontScale, color, thickness)

    break
cv2.imshow('Webcam', img)
cv2.waitKey(0)
cv2.destroyAllWindows()