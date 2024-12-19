import serial
import time
import cv2

cap = cv2.VideoCapture(0)

# 원의 위치와 반지름 설정
circles = [
    {"center": (180, 450), "radius": 20},
    {"center": (130, 450), "radius": 20},
    {"center": (80, 450), "radius": 20},
    {"center": (30, 450), "radius": 20},
]


# 직렬 포트 연결 설정
py_serial = serial.Serial(
    port='COM6',  # 실제 포트에 맞게 수정
    baudrate=9600
)

time.sleep(2)  # 직렬 통신 초기화를 위한 대기 시간

try:
    #distance = "데이터 없음"  # 초기값 설정

    while True:
        success, img = cap.read()
        if not success:
            print("웹캠을 읽을 수 없습니다.")
            break
        
        # 직렬 포트에서 거리 데이터 읽기
        if py_serial.in_waiting > 0:
            distance = py_serial.readline().decode('utf-8').strip()
            distance = int(float(distance))
  
        cv2.putText(img, str(distance), (300,450), cv2.FONT_HERSHEY_SIMPLEX, 2, (0,0,255), 1)
        print(distance)    
        
        if distance is not None:
            fill_count = 0
    
            if distance < 50:
                fill_count = 4  # 50 미만일 때 4개 원 채우기
            elif distance < 75:
                fill_count = 3  # 75 미만일 때 3개 원 채우기
            elif distance < 90:
                fill_count = 2  # 90 미만일 때 2개 원 채우기
            elif distance < 120:
                fill_count = 1  # 120 미만일 때 1개 원 채우기

            for i, circle in enumerate(circles):
                if i < fill_count:
                    cv2.circle(img, circle["center"], circle["radius"], (0, 0, 255), -1)  # 원 채우기
                else:
                    cv2.circle(img, circle["center"], circle["radius"], (0, 0, 255), 2)  # 원 테두리만 그리기
            
        #webcam 거꾸로 설치시 아니면 주석 처리 :        
        #img = cv2.flip(img, 0)
        
        img = cv2.resize(img, (1200, 760))
        cv2.imshow("webcam", img)     
        if cv2.waitKey(1) == ord('q'):
            break 

except KeyboardInterrupt:
    print("프로그램이 중단되었습니다.")
finally:
    py_serial.close()
    cap.release()
    cv2.destroyAllWindows()
