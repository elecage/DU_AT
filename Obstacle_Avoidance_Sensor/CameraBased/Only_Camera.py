import cv2

cap = cv2.VideoCapture(0) # 숫자를 변경하면서 사용 가능한 카메라를 찾기

while True:
    success, img = cap.read()
    if not success:
        print("웹캠을 읽을 수 없습니다. Cv2.VideoCapture(1)로 변경해보세요.")
        break
    
    #webcam 거꾸로 설치시 사용, 아니면 주석 처리 :        
    img = cv2.flip(img, 0)
    
    cv2.imshow("webcam", img)     
    if cv2.waitKey(1) == ord('q'):
        break 
    
cap.release()
cv2.destroyAllWindows()

