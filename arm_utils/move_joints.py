"""
# Импортируем необходимые библиотеки
# библиотека работы с ROS
"""
import rospy

# Данный тип сообщений необходим для trajectory_msgs
from std_msgs.msg import *

# Сообщения для описания траектории движения
from trajectory_msgs.msg import *

class ControlJoints:
    def __init__(self, arm_type):
        # Сохраним тип Rooky
        self._arm_type = arm_type
        self._positions = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

        # Получим частоту от сервера параметров ROS.
        # Если не удалось, по умолчанию 20Гц
        self._rate = rospy.get_param('~rate', 20)

        # Список для движения всех суставов
        self._joint_names = [str(arm_type) + '_arm_1_joint',
                             str(arm_type) + '_arm_2_joint',
                             str(arm_type) + '_arm_3_joint',
                             str(arm_type) + '_arm_4_joint',
                             str(arm_type) + '_arm_5_joint',
                             str(arm_type) + '_arm_6_joint',
                             str(arm_type) + '_arm_7_joint']

        # Подключимся к топику как Publisher
        # Тип сообщений - JointTrajectory
        # Максимальное число сообщений в очереди на отправку - 1
        self._cmd_pub = rospy.Publisher(str(arm_type) + '_arm_controller/command',
                                        JointTrajectory,
                                        queue_size=1)

        # Задержка на 1 секунду
        rospy.sleep(1)

        # Время движения до указанной точки в секундах
        interval_to_point = 1

        self._positions = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        self.move_all_joints(interval_to_point)

        rospy.sleep(2.0)

    def spinOnce(self):
         # Время движения до указанной точки в секундах
        interval_to_point = 2
        import numpy as np
        for i in np.arange(0.9, 1.0, 0.1):
            for j in np.arange(0.8, 0.9, 0.1):
                for k in np.arange (0.4, 0.5, 0.1):
                    self._positions = [i, k, 0.0, j, 0.0, -0.15, 0.0]
                    self.move_all_joints(interval_to_point)
                    rospy.sleep(5)
                    self._positions = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
                    self.move_all_joints(interval_to_point)
                    rospy.sleep(5)



        # Движения одного сустава
        #self.move_joint(str(self._arm_type) + '_arm_1_joint', 1.0, 3)
        #rospy.sleep(5)

    def rate(self):
        return self._rate

    # Функция отправки в желаемую позицию всех суставов Rooky
    def move_all_joints(self, interval_to_point=0.1):
        # Создадим объект траектории
        traj = JointTrajectory()

        # Назначим имена суставов
        traj.joint_names = self._joint_names

        # Создадим объект точки, которой должен достичь манипулятор Rooky.
        point = JointTrajectoryPoint()

        # Назначим позиции для движения каждого сустава
        point.positions = self._positions

        # Укажем как долго должны двигаться манипулятор до указанного положения
        point.time_from_start = rospy.Duration(interval_to_point)
        traj.points.append(point)

        # Отправим сообщение в топик
        self._cmd_pub.publish(traj)

    # Функция отправки в желаемую позицию одного сустава Rooky
    def move_joint(self, joint_name, position, interval_to_point=0.1):
        traj = JointTrajectory()
        traj.joint_names.append(joint_name)
        point = JointTrajectoryPoint()

        point.positions.append(position)

        point.time_from_start = rospy.Duration(interval_to_point)
        traj.points.append(point)

        self._cmd_pub.publish(traj)

    def reset_joints(self):
         # Время движения до указанной точки в секундах
        interval_to_point = 3
        self._positions[3] = 0.5
        self._positions[0] = 0.3
        self.move_all_joints(1)
        rospy.sleep(0.5)
        self._positions = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        self.move_all_joints(interval_to_point)

    @property
    def positions(self):
        return self._positions
# rospy.init_node('joint_control_sim_test')
# node = ControlJoints('left')
# node.reset_joints()
# # node.spinOnce()